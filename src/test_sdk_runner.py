#!/usr/bin/env python3
"""Unit tests for sdk_runner with a mocked cursor_sdk module."""

from __future__ import annotations

import os
import sys
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdk_runner


@dataclass
class _FakeBlock:
    type: str
    text: str


@dataclass
class _FakeAssistantPayload:
    content: list[_FakeBlock]


@dataclass
class _FakeAssistantMessage:
    type: str
    message: _FakeAssistantPayload


@dataclass
class _FakeToolMessage:
    type: str
    name: str
    status: str
    args: dict[str, str] = field(default_factory=dict)


@dataclass
class _FakeWaitResult:
    status: str
    result: str
    model: object | None = None


@dataclass
class _FakeModel:
    id: str


class _FakeRun:
    def __init__(self) -> None:
        self.status = "running"
        self._cancelled = False
        self._messages = [
            _FakeAssistantMessage(
                type="assistant",
                message=_FakeAssistantPayload(
                    content=[_FakeBlock(type="text", text="hello from sdk")],
                ),
            ),
            _FakeToolMessage(
                type="tool_call",
                name="Read",
                status="running",
                args={"path": "src/foo.py"},
            ),
        ]

    def messages(self) -> Iterator[object]:
        yield from self._messages

    def cancel(self) -> None:
        self._cancelled = True
        self.status = "cancelled"

    def wait(self) -> _FakeWaitResult:
        self.status = "finished"
        return _FakeWaitResult(
            status="finished",
            result="final sdk answer",
            model=_FakeModel(id="composer-2.5"),
        )


class _FakeAgent:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    @classmethod
    def create(cls, *args: object, **kwargs: object) -> _FakeAgent:
        return cls(*args, **kwargs)

    def __enter__(self) -> _FakeAgent:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def send(self, _prompt: str, _options: object = None) -> _FakeRun:
        return _FakeRun()


@dataclass
class _FakeLocalAgentOptions:
    cwd: str


@dataclass
class _FakeSendOptions:
    mode: str | None = None


def _install_fake_sdk() -> None:
    fake = types.ModuleType("cursor_sdk")
    fake.Agent = _FakeAgent  # type: ignore[attr-defined]
    fake.LocalAgentOptions = _FakeLocalAgentOptions  # type: ignore[attr-defined]
    fake.SendOptions = _FakeSendOptions  # type: ignore[attr-defined]
    sys.modules["cursor_sdk"] = fake


class SdkRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_key = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "test-key"
        _install_fake_sdk()
        sdk_runner._import_sdk = lambda: sys.modules["cursor_sdk"]  # type: ignore[method-assign]

    def tearDown(self) -> None:
        if self._prev_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prev_key
        sys.modules.pop("cursor_sdk", None)

    def test_missing_api_key(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        result = sdk_runner.run_sdk(
            prompt="hi",
            cwd=".",
            mode="ask",
            model="composer-2.5",
            prefer_fast=False,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=30,
            require_diff=False,
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["backend"], "sdk")
        self.assertIn("CURSOR_API_KEY", result["result"])

    def test_progress_callbacks_and_envelope(self) -> None:
        progress: list[tuple[float, str]] = []
        statuses: list[dict[str, object]] = []

        result = sdk_runner.run_sdk(
            prompt="summarize repo",
            cwd=".",
            mode="plan",
            model="composer-2.5",
            prefer_fast=False,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=30,
            require_diff=False,
            on_progress=lambda value, message: progress.append((value, message)),
            on_status=lambda event: statuses.append(event),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backend"], "sdk")
        self.assertEqual(result["model"], "composer-2.5")
        self.assertEqual(result["result"], "final sdk answer")
        self.assertEqual(result["tools"], 1)
        self.assertIn("assistant:", result["progress_summary"])
        self.assertIn("tool #1", result["progress_summary"])
        self.assertTrue(any("assistant:" in msg for _, msg in progress))
        self.assertTrue(any(event.get("type") == "tool_call" for event in statuses))
        self.assertEqual(result["exit_code"], 0)

    def test_cancel_sdk_run(self) -> None:
        run = _FakeRun()
        job_id = "job-cancel-test"
        sdk_runner._active_runs[job_id] = run
        self.assertTrue(sdk_runner.cancel_sdk_run(job_id))
        self.assertTrue(run._cancelled)
        self.assertFalse(sdk_runner.cancel_sdk_run("missing"))


class SdkImportTests(unittest.TestCase):
    def test_missing_sdk_package(self) -> None:
        sys.modules.pop("cursor_sdk", None)
        with patch.dict(os.environ, {"CURSOR_API_KEY": "test-key"}, clear=False):
            original = sdk_runner._import_sdk
            sdk_runner._import_sdk = lambda: None  # type: ignore[method-assign]
            try:
                result = sdk_runner.run_sdk(
                    prompt="hi",
                    cwd=".",
                    mode="ask",
                    model="composer-2.5",
                    prefer_fast=False,
                    force=False,
                    worktree=None,
                    skip_preflight=True,
                    continue_session=False,
                    timeout=30,
                    require_diff=False,
                )
            finally:
                sdk_runner._import_sdk = original  # type: ignore[method-assign]
        self.assertEqual(result["status"], "error")
        self.assertIn("cursor-sdk is not installed", result["result"])


if __name__ == "__main__":
    unittest.main()
