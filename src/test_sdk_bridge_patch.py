#!/usr/bin/env python3
"""Tests for Windows bridge discovery patch."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdk_bridge_patch


class SdkBridgePatchTests(unittest.TestCase):
    def tearDown(self) -> None:
        sdk_bridge_patch._PATCHED = False

    def test_noop_on_posix(self) -> None:
        sdk_bridge_patch._PATCHED = False
        with patch.object(sdk_bridge_patch.os, "name", "posix"):
            self.assertFalse(sdk_bridge_patch.apply_windows_bridge_discovery_patch())

    def test_applies_on_windows(self) -> None:
        sdk_bridge_patch._PATCHED = False
        fake = types.ModuleType("cursor_sdk._bridge")

        def original_read(process: object, timeout: float) -> dict[str, str]:
            return {"ok": "no"}

        fake._read_discovery = original_read  # type: ignore[attr-defined]
        fake.parse_discovery_line = lambda line: None  # type: ignore[attr-defined]
        fake.CursorSDKError = RuntimeError  # type: ignore[attr-defined]

        with (
            patch.object(sdk_bridge_patch.os, "name", "nt"),
            patch.dict(sys.modules, {"cursor_sdk._bridge": fake}),
        ):
            self.assertTrue(sdk_bridge_patch.apply_windows_bridge_discovery_patch())
            self.assertTrue(getattr(fake, "_cursor_headless_discovery_patched"))
            self.assertIsNot(fake._read_discovery, original_read)
            # second apply is idempotent
            self.assertFalse(sdk_bridge_patch.apply_windows_bridge_discovery_patch())


if __name__ == "__main__":
    unittest.main()
