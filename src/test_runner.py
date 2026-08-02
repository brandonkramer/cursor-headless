#!/usr/bin/env python3
"""Unit tests for backend resolution."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runner


class ResolveBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_backend = os.environ.get("CURSOR_HEADLESS_BACKEND")
        self._prev_key = os.environ.get("CURSOR_API_KEY")
        os.environ.pop("CURSOR_HEADLESS_BACKEND", None)
        os.environ.pop("CURSOR_API_KEY", None)

    def tearDown(self) -> None:
        if self._prev_backend is None:
            os.environ.pop("CURSOR_HEADLESS_BACKEND", None)
        else:
            os.environ["CURSOR_HEADLESS_BACKEND"] = self._prev_backend
        if self._prev_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prev_key

    def test_explicit_arg_wins(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test"
        os.environ["CURSOR_HEADLESS_BACKEND"] = "sdk"
        self.assertEqual(runner.resolve_backend("cli"), "cli")
        self.assertEqual(runner.resolve_backend("sdk"), "sdk")

    def test_env_override_wins_over_auto(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test"
        os.environ["CURSOR_HEADLESS_BACKEND"] = "cli"
        self.assertEqual(runner.resolve_backend(None), "cli")

    def test_auto_cli_without_key(self) -> None:
        self.assertEqual(runner.resolve_backend(None), "cli")

    def test_auto_sdk_with_key(self) -> None:
        os.environ["CURSOR_API_KEY"] = "crsr_test"
        self.assertEqual(runner.resolve_backend(None), "sdk")


if __name__ == "__main__":
    unittest.main()
