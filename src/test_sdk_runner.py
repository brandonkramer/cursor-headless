#!/usr/bin/env python3
"""Unit tests for sdk_runner with a mocked cursor_sdk module."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdk_runner
import sdk_sessions


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


_CREATE_CALLS: list[dict[str, object]] = []
_SEND_CALLS: list[tuple[str, object]] = []
_RESUME_CALLS: list[tuple[str, object]] = []
_RESUME_FAIL_IDS: set[str] = set()


class _FakeAgent:
    agent_id = "agent-test-123"

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    @classmethod
    def create(cls, *args: object, **kwargs: object) -> _FakeAgent:
        _CREATE_CALLS.append(dict(kwargs))
        return cls(*args, **kwargs)

    @classmethod
    def resume(cls, agent_id: str, options: object = None) -> _FakeAgent:
        _RESUME_CALLS.append((agent_id, options))
        if agent_id in _RESUME_FAIL_IDS:
            raise RuntimeError("resume failed")
        inst = cls()
        inst.agent_id = agent_id
        return inst

    def __enter__(self) -> _FakeAgent:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def send(self, prompt: str, options: object = None) -> _FakeRun:
        _SEND_CALLS.append((prompt, options))
        return _FakeRun()


@dataclass
class _FakeLocalAgentOptions:
    cwd: str | None = None


@dataclass
class _FakeLocalSendOptions:
    force: bool | None = None


@dataclass
class _FakeSendOptions:
    mode: str | None = None
    local: _FakeLocalSendOptions | dict[str, object] | None = None


@dataclass
class _FakeAgentOptions:
    api_key: str | None = None


def _install_fake_sdk(*, include_local_send: bool = True) -> None:
    fake = types.ModuleType("cursor_sdk")
    fake.Agent = _FakeAgent  # type: ignore[attr-defined]
    fake.LocalAgentOptions = _FakeLocalAgentOptions  # type: ignore[attr-defined]
    fake.AgentOptions = _FakeAgentOptions  # type: ignore[attr-defined]
    fake.SendOptions = _FakeSendOptions  # type: ignore[attr-defined]
    if include_local_send:
        fake.LocalSendOptions = _FakeLocalSendOptions  # type: ignore[attr-defined]
    sys.modules["cursor_sdk"] = fake


class SdkRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_key = os.environ.get("CURSOR_API_KEY")
        self._prev_session_dir = os.environ.get("CURSOR_HEADLESS_SDK_SESSION_DIR")
        self._prev_agent_id = os.environ.get("CURSOR_HEADLESS_SDK_AGENT_ID")
        self._session_tmp = tempfile.TemporaryDirectory()
        os.environ["CURSOR_API_KEY"] = "test-key"
        os.environ["CURSOR_HEADLESS_SDK_SESSION_DIR"] = self._session_tmp.name
        os.environ.pop("CURSOR_HEADLESS_SDK_AGENT_ID", None)
        _CREATE_CALLS.clear()
        _SEND_CALLS.clear()
        _RESUME_CALLS.clear()
        _RESUME_FAIL_IDS.clear()
        _install_fake_sdk()
        sdk_runner._import_sdk = lambda: sys.modules["cursor_sdk"]  # type: ignore[method-assign]

    def tearDown(self) -> None:
        if self._prev_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prev_key
        if self._prev_session_dir is None:
            os.environ.pop("CURSOR_HEADLESS_SDK_SESSION_DIR", None)
        else:
            os.environ["CURSOR_HEADLESS_SDK_SESSION_DIR"] = self._prev_session_dir
        if self._prev_agent_id is None:
            os.environ.pop("CURSOR_HEADLESS_SDK_AGENT_ID", None)
        else:
            os.environ["CURSOR_HEADLESS_SDK_AGENT_ID"] = self._prev_agent_id
        self._session_tmp.cleanup()
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

    def test_force_rejected_for_ask_mode(self) -> None:
        result = sdk_runner.run_sdk(
            prompt="hi",
            cwd=".",
            mode="ask",
            model="composer-2.5",
            prefer_fast=False,
            force=True,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=30,
            require_diff=False,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("force is only allowed with mode default", result["result"])
        self.assertEqual(_CREATE_CALLS, [])
        self.assertEqual(_SEND_CALLS, [])

    def test_force_passed_via_local_send_options(self) -> None:
        result = sdk_runner.run_sdk(
            prompt="edit file",
            cwd=".",
            mode="default",
            model="composer-2.5",
            prefer_fast=False,
            force=True,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=30,
            require_diff=False,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(_SEND_CALLS), 1)
        _prompt, options = _SEND_CALLS[0]
        self.assertNotIn("Auto-approve shell commands", _prompt)
        self.assertIsInstance(options, _FakeSendOptions)
        assert isinstance(options, _FakeSendOptions)
        self.assertEqual(options.mode, "agent")
        self.assertIsInstance(options.local, _FakeLocalSendOptions)
        assert isinstance(options.local, _FakeLocalSendOptions)
        self.assertTrue(options.local.force)

    def test_force_prompt_fallback_without_local_send_options(self) -> None:
        _install_fake_sdk(include_local_send=False)
        result = sdk_runner.run_sdk(
            prompt="edit file",
            cwd=".",
            mode="default",
            model="composer-2.5",
            prefer_fast=False,
            force=True,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=30,
            require_diff=False,
        )
        self.assertEqual(result["status"], "ok")
        prompt, options = _SEND_CALLS[0]
        self.assertIn("Auto-approve shell commands", prompt)
        assert isinstance(options, _FakeSendOptions)
        self.assertEqual(options.local, {"force": True})

    def test_worktree_uses_git_worktree_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"},
            )

            result = sdk_runner.run_sdk(
                prompt="edit in worktree",
                cwd=str(repo),
                mode="default",
                model="composer-2.5",
                prefer_fast=False,
                force=False,
                worktree="sdk-test-wt",
                skip_preflight=True,
                continue_session=False,
                timeout=30,
                require_diff=False,
            )

            self.assertEqual(result["status"], "ok")
            expected = repo / ".cursor-headless" / "worktrees" / "sdk-test-wt"
            self.assertTrue(expected.is_dir())
            self.assertEqual(len(_CREATE_CALLS), 1)
            local_opts = _CREATE_CALLS[0]["local"]
            self.assertIsInstance(local_opts, _FakeLocalAgentOptions)
            assert isinstance(local_opts, _FakeLocalAgentOptions)
            self.assertEqual(local_opts.cwd, str(expected.resolve()))
            prompt, _options = _SEND_CALLS[0]
            self.assertNotIn("isolated git worktree", prompt)

    def test_worktree_requires_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = sdk_runner.run_sdk(
                prompt="edit",
                cwd=tmp,
                mode="default",
                model="composer-2.5",
                prefer_fast=False,
                force=False,
                worktree="nope",
                skip_preflight=True,
                continue_session=False,
                timeout=30,
                require_diff=False,
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("requires a git repository", result["result"])
            self.assertEqual(_CREATE_CALLS, [])

    def test_cancel_sdk_run(self) -> None:
        run = _FakeRun()
        job_id = "job-cancel-test"
        sdk_runner._active_runs[job_id] = run
        self.assertTrue(sdk_runner.cancel_sdk_run(job_id))
        self.assertTrue(run._cancelled)
        self.assertFalse(sdk_runner.cancel_sdk_run("missing"))

    def test_continue_session_false_uses_create(self) -> None:
        workspace = str(Path(".").resolve())
        result = sdk_runner.run_sdk(
            prompt="hi",
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
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(_CREATE_CALLS), 1)
        self.assertEqual(_RESUME_CALLS, [])
        self.assertEqual(
            sdk_sessions.load_stored_agent_id(workspace=workspace, mode="plan"),
            "agent-test-123",
        )

    def test_continue_session_true_resumes_stored_id(self) -> None:
        workspace = str(Path(".").resolve())
        sdk_sessions.save_stored_agent_id(
            workspace=workspace,
            mode="plan",
            agent_id="agent-stored-456",
        )
        result = sdk_runner.run_sdk(
            prompt="follow up",
            cwd=".",
            mode="plan",
            model="composer-2.5",
            prefer_fast=False,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=True,
            timeout=30,
            require_diff=False,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(_CREATE_CALLS, [])
        self.assertEqual(_RESUME_CALLS, [("agent-stored-456", _FakeAgentOptions(api_key="test-key"))])

    def test_continue_session_env_agent_id_override(self) -> None:
        os.environ["CURSOR_HEADLESS_SDK_AGENT_ID"] = "agent-env-789"
        result = sdk_runner.run_sdk(
            prompt="follow up",
            cwd=".",
            mode="ask",
            model="composer-2.5",
            prefer_fast=False,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=True,
            timeout=30,
            require_diff=False,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(_CREATE_CALLS, [])
        self.assertEqual(_RESUME_CALLS, [("agent-env-789", _FakeAgentOptions(api_key="test-key"))])

    def test_continue_session_resume_failure_falls_back_to_create(self) -> None:
        workspace = str(Path(".").resolve())
        _RESUME_FAIL_IDS.add("agent-bad")
        sdk_sessions.save_stored_agent_id(
            workspace=workspace,
            mode="default",
            agent_id="agent-bad",
        )
        progress: list[tuple[float, str]] = []
        result = sdk_runner.run_sdk(
            prompt="try again",
            cwd=".",
            mode="default",
            model="composer-2.5",
            prefer_fast=False,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=True,
            timeout=30,
            require_diff=False,
            on_progress=lambda value, message: progress.append((value, message)),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(_RESUME_CALLS, [("agent-bad", _FakeAgentOptions(api_key="test-key"))])
        self.assertEqual(len(_CREATE_CALLS), 1)
        self.assertIn("falling back to Agent.create", result["stderr"])
        self.assertTrue(any("falling back to Agent.create" in msg for _, msg in progress))


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
