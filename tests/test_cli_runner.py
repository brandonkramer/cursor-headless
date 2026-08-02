#!/usr/bin/env python3
"""Unit tests for cli_runner stream-json parsing and timeout handling."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli_runner import parse_ndjson_line, run_cli, _StreamState  # noqa: PLC2701


def _write_fake_agent(
    bin_dir: Path,
    *,
    body: str,
) -> None:
    """Install a fake cursor-agent on PATH (Python script; .cmd launcher on Windows)."""
    script = textwrap.dedent(body).lstrip()
    if os.name == "nt":
        py_path = bin_dir / "cursor-agent-impl.py"
        py_path.write_text(script, encoding="utf-8", newline="\n")
        cmd_path = bin_dir / "cursor-agent.cmd"
        cmd_path.write_text(
            f'@echo off\r\n"{sys.executable}" "{py_path}" %*\r\n',
            encoding="utf-8",
            newline="\r\n",
        )
        return
    agent_path = bin_dir / "cursor-agent"
    agent_path.write_text(script, encoding="utf-8", newline="\n")
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ParseNdjsonLineTests(unittest.TestCase):
    def test_result_event_sets_authoritative_text(self) -> None:
        state = _StreamState()
        line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "final answer",
            }
        )
        parse_ndjson_line(line, state)
        self.assertEqual(state.result_text, "final answer")
        self.assertEqual(state.progress, 1.0)

    def test_non_json_lines_collected_as_tail(self) -> None:
        state = _StreamState()
        parse_ndjson_line("--- wrapper git evidence ---", state)
        self.assertEqual(state.non_json_lines, ["--- wrapper git evidence ---"])


class RunCliIntegrationTests(unittest.TestCase):
    def test_successful_stream_json_run(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            events = [
                {
                    "type": "system",
                    "subtype": "init",
                    "model": "fake-model",
                    "session_id": "abc",
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "working"}],
                    },
                },
                {
                    "type": "tool_call",
                    "subtype": "started",
                    "tool_call": {
                        "readToolCall": {"args": {"path": "src/foo.py"}},
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "hello world",
                    "duration_ms": 50,
                },
            ]
            serialized = [json.dumps(e) for e in events]
            _write_fake_agent(
                bin_dir,
                body=f"""\
                #!/usr/bin/env python3
                import sys
                for line in {serialized!r}:
                    print(line, flush=True)
                sys.exit(0)
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

            with patch.dict(os.environ, env, clear=True):
                result = run_cli(
                    prompt="say hello",
                    cwd=tmp,
                    mode="ask",
                    model="cursor-grok-4.5-high",
                    prefer_fast=False,
                    force=False,
                    worktree=None,
                    skip_preflight=True,
                    continue_session=False,
                    timeout=30.0,
                    require_diff=False,
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["backend"], "cli")
            self.assertIn("hello world", result["result"])
            self.assertIn("wrapper git evidence", result["result"])
            self.assertEqual(result["tools"], 1)
            self.assertIn("fake-model", result["model"])
            self.assertGreater(result["elapsed_s"], 0.0)
            self.assertIn("tool #1", result["progress_summary"])

    def test_timeout_returns_timeout_status(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _write_fake_agent(
                bin_dir,
                body="""\
                #!/usr/bin/env python3
                import json, sys, time
                print(json.dumps({"type":"system","subtype":"init","model":"slow"}), flush=True)
                time.sleep(3)
                print(json.dumps({"type":"result","subtype":"success","result":"late"}), flush=True)
                sys.exit(0)
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

            with patch.dict(os.environ, env, clear=True):
                result = run_cli(
                    prompt="slow task",
                    cwd=tmp,
                    mode="ask",
                    model="cursor-grok-4.5-high",
                    prefer_fast=False,
                    force=False,
                    worktree=None,
                    skip_preflight=True,
                    continue_session=False,
                    timeout=0.5,
                    require_diff=False,
                )

            self.assertEqual(result["status"], "timeout")
            self.assertIn("treat as no result", result["result"])


if __name__ == "__main__":
    unittest.main()
