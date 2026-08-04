#!/usr/bin/env python3
"""Preflight / Windows .cmd capture helpers for cursor_headless.py."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WRAPPER = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "cursor-headless"
    / "scripts"
    / "cursor_headless.py"
)


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("cursor_headless_wrapper", WRAPPER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WrapAgentCmdTests(unittest.TestCase):
    def test_wraps_cmd_shim_on_windows(self) -> None:
        mod = _load_wrapper()
        argv = [r"C:\Users\Administrator\AppData\Local\cursor-agent\cursor-agent.cmd", "models"]
        with patch.object(mod.os, "name", "nt"):
            wrapped = mod.wrap_agent_cmd(argv)
        self.assertEqual(wrapped[0], "cmd.exe")
        self.assertIn("/c", wrapped)
        self.assertIn("cursor-agent.cmd", wrapped[-1])
        self.assertIn("models", wrapped[-1])

    def test_leaves_non_cmd_alone(self) -> None:
        mod = _load_wrapper()
        argv = ["/usr/local/bin/cursor-agent", "models"]
        with patch.object(mod.os, "name", "nt"):
            self.assertEqual(mod.wrap_agent_cmd(argv), argv)


class EnsurePreflightTests(unittest.TestCase):
    def test_accepts_model_listed_only_on_stderr(self) -> None:
        mod = _load_wrapper()
        catalog = "Available models\ncursor-grok-4.5-high - Cursor Grok 4.5\n"

        def fake_run_quiet(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
            del timeout
            action = cmd[-1] if cmd else ""
            if action == "--version":
                return subprocess.CompletedProcess(cmd, 0, stdout="2026.07.23\n", stderr="")
            if action == "status":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="✓ Logged in as x\n")
            if action == "models":
                # Simulate Windows .cmd capture quirk: catalog on stderr only.
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=catalog)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unexpected")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cache = Path(tmp) / "preflight.json"
            with (
                patch.object(mod, "PREFLIGHT_CACHE", cache),
                patch.object(mod, "cursor_agent_bin", return_value="cursor-agent.cmd"),
                patch.object(mod, "run_quiet", side_effect=fake_run_quiet),
            ):
                mod.ensure_preflight("cursor-grok-4.5-high", force=True, skip=False)
            data = cache.read_text(encoding="utf-8")
            self.assertIn("cursor-grok-4.5-high", data)

    def test_empty_models_output_is_distinct_error(self) -> None:
        mod = _load_wrapper()

        def fake_run_quiet(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
            del timeout
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch.object(mod, "cursor_agent_bin", return_value=r"C:\x\cursor-agent.cmd"),
            patch.object(mod, "run_quiet", side_effect=fake_run_quiet),
            patch.object(mod, "cached_preflight_ok", return_value=False),
        ):
            with self.assertRaises(SystemExit) as ctx:
                mod.ensure_preflight("cursor-grok-4.5-high", force=True, skip=False)
        self.assertIn("produced no captured output", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
