#!/usr/bin/env python3
"""Unit tests for sdk_cloud_runner with a mocked cursor_sdk module."""

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

import sdk_cloud_runner
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
class _FakeBranch:
    pr_url: str


@dataclass
class _FakeGit:
    branches: list[_FakeBranch]


@dataclass
class _FakeWaitResult:
    status: str
    result: str
    model: object | None = None
    git: object | None = None


@dataclass
class _FakeModel:
    id: str


class _FakeRun:
    def __init__(self) -> None:
        self.status = "running"
        self._messages = [
            _FakeAssistantMessage(
                type="assistant",
                message=_FakeAssistantPayload(
                    content=[_FakeBlock(type="text", text="cloud plan ok")],
                ),
            ),
        ]

    def messages(self) -> Iterator[object]:
        yield from self._messages

    def cancel(self) -> None:
        self.status = "cancelled"

    def wait(self) -> _FakeWaitResult:
        self.status = "finished"
        return _FakeWaitResult(
            status="finished",
            result="cloud final",
            model=_FakeModel(id="composer-2.5"),
            git=_FakeGit(branches=[_FakeBranch(pr_url="https://github.com/o/r/pull/1")]),
        )


class _FakeRunPrReview(_FakeRun):
    def wait(self) -> _FakeWaitResult:
        self.status = "finished"
        return _FakeWaitResult(
            status="finished",
            result='{"summary": "LGTM", "event": "COMMENT", "comments": []}',
            model=_FakeModel(id="cursor-grok-4.5-high"),
        )


_CREATE_CALLS: list[dict[str, object]] = []
_SEND_CALLS: list[tuple[str, object]] = []
_RESUME_CALLS: list[str] = []


class _FakeAgentCM:
    def __init__(self, agent_id: str = "bc-test-agent") -> None:
        self.agent_id = agent_id

    def __enter__(self) -> _FakeAgentCM:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def send(self, prompt: str, options: object = None) -> _FakeRun:
        _SEND_CALLS.append((prompt, options))
        return _FakeRun()


class _FakeAgent:
    @staticmethod
    def create(**kwargs: object) -> _FakeAgentCM:
        _CREATE_CALLS.append(dict(kwargs))
        return _FakeAgentCM()

    @staticmethod
    def resume(agent_id: str, options: object = None) -> _FakeAgentCM:
        _RESUME_CALLS.append(agent_id)
        return _FakeAgentCM(agent_id=agent_id)


@dataclass
class _FakeCloudRepo:
    url: str
    starting_ref: str | None = None
    pr_url: str | None = None


@dataclass
class _FakeCloudEnv:
    type: str = "cloud"
    name: str | None = None


@dataclass
class _FakeCloudOpts:
    env: object = None
    repos: list[object] = field(default_factory=list)
    work_on_current_branch: bool | None = None
    auto_create_pr: bool | None = None
    skip_reviewer_request: bool | None = None
    env_vars: dict[str, str] | None = None


@dataclass
class _FakeSendOpts:
    mode: str | None = None
    model: object = None
    cloud: object = None


@dataclass
class _FakeCloudSendOpts:
    env_vars: dict[str, str] | None = None


@dataclass
class _FakeModelSelection:
    id: str
    params: list[object] = field(default_factory=list)


@dataclass
class _FakeParam:
    id: str
    value: str


def _install_fake_sdk() -> types.ModuleType:
    fake = types.ModuleType("cursor_sdk")
    fake.Agent = _FakeAgent  # type: ignore[attr-defined]
    fake.AgentOptions = lambda **kw: kw  # type: ignore[attr-defined,misc]
    fake.CloudAgentOptions = _FakeCloudOpts  # type: ignore[attr-defined]
    fake.CloudRepository = _FakeCloudRepo  # type: ignore[attr-defined]
    fake.CloudEnvironment = _FakeCloudEnv  # type: ignore[attr-defined]
    fake.CloudSendOptions = _FakeCloudSendOpts  # type: ignore[attr-defined]
    fake.SendOptions = _FakeSendOpts  # type: ignore[attr-defined]
    fake.ModelSelection = _FakeModelSelection  # type: ignore[attr-defined]
    fake.ModelParameterValue = _FakeParam  # type: ignore[attr-defined]
    return fake


class SdkCloudRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        _CREATE_CALLS.clear()
        _SEND_CALLS.clear()
        _RESUME_CALLS.clear()
        self._prev_key = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "crsr_test"
        self._tmpdir = Path(self._testMethodName + "-sess")
        # use temp session dir
        import tempfile

        self._session_root = Path(tempfile.mkdtemp(prefix="cloud-sess-"))
        self._sess_env = patch.dict(
            os.environ, {"CURSOR_HEADLESS_SDK_SESSION_DIR": str(self._session_root)}
        )
        self._sess_env.start()

    def tearDown(self) -> None:
        self._sess_env.stop()
        if self._prev_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prev_key

    def test_missing_key(self) -> None:
        os.environ.pop("CURSOR_API_KEY", None)
        result = sdk_cloud_runner.run_cloud_sdk(
            kind="plan",
            prompt="x",
            repo_url="https://github.com/o/r",
            model="composer-2.5",
            prefer_fast=True,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("CURSOR_API_KEY", result["result"])

    def test_missing_repo(self) -> None:
        result = sdk_cloud_runner.run_cloud_sdk(
            kind="plan",
            prompt="x",
            repo_url="  ",
            model="composer-2.5",
            prefer_fast=False,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("repo_url", result["result"])

    def test_implement_create_and_pr(self) -> None:
        fake = _install_fake_sdk()
        with patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake):
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="implement",
                prompt="Add logging",
                repo_url="https://github.com/o/r",
                model="composer-2.5",
                prefer_fast=True,
                auto_create_pr=True,
                starting_ref="main",
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backend"], "sdk-cloud")
        self.assertEqual(result["runtime"], "cloud")
        self.assertEqual(result["agent_id"], "bc-test-agent")
        self.assertEqual(result["pr_url"], "https://github.com/o/r/pull/1")
        self.assertEqual(result["result"], "cloud final")
        self.assertEqual(len(_CREATE_CALLS), 1)
        cloud = _CREATE_CALLS[0]["cloud"]
        assert isinstance(cloud, _FakeCloudOpts)
        self.assertTrue(cloud.auto_create_pr)
        self.assertEqual(cloud.repos[0].url, "https://github.com/o/r")  # type: ignore[union-attr]
        prompt, opts = _SEND_CALLS[0]
        self.assertEqual(prompt, "Add logging")
        assert isinstance(opts, _FakeSendOpts)
        self.assertEqual(opts.mode, "agent")

    def test_review_prompt_prefix_and_plan_mode(self) -> None:
        fake = _install_fake_sdk()
        with patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake):
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="review",
                prompt="Check auth",
                repo_url="https://github.com/o/r",
                model="cursor-grok-4.5-high",
                prefer_fast=False,
                pr_url="https://github.com/o/r/pull/9",
            )
        self.assertEqual(result["status"], "ok")
        prompt, opts = _SEND_CALLS[0]
        self.assertIn("Read-only code review", prompt)
        self.assertIn("Check auth", prompt)
        assert isinstance(opts, _FakeSendOpts)
        self.assertEqual(opts.mode, "plan")
        cloud = _CREATE_CALLS[0]["cloud"]
        assert isinstance(cloud, _FakeCloudOpts)
        self.assertEqual(cloud.repos[0].pr_url, "https://github.com/o/r/pull/9")  # type: ignore[union-attr]

    def test_review_pr_delivery_appends_json_instructions(self) -> None:
        from pr_review_publish import PublishResult

        fake = _install_fake_sdk()
        with (
            patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake),
            patch.object(sdk_cloud_runner, "publish_pr_review") as mock_pub,
        ):
            mock_pub.return_value = PublishResult(ok=True, message="posted")
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="review",
                prompt="Check auth",
                repo_url="https://github.com/o/r",
                model="cursor-grok-4.5-high",
                prefer_fast=False,
                pr_url="https://github.com/o/r/pull/9",
                delivery="pr_review",
                review_event="REQUEST_CHANGES",
            )
        self.assertEqual(result["status"], "ok")
        prompt, _opts = _SEND_CALLS[0]
        self.assertIn("Return ONLY a single JSON object", prompt)
        self.assertIn("REQUEST_CHANGES", prompt)

    def test_review_pr_delivery_publishes(self) -> None:
        from pr_review_publish import PublishResult

        fake = _install_fake_sdk()

        def send_pr_review(
            self: _FakeAgentCM,
            prompt: str,
            options: object = None,
        ) -> _FakeRunPrReview:
            _SEND_CALLS.append((prompt, options))
            return _FakeRunPrReview()

        with (
            patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake),
            patch.object(_FakeAgentCM, "send", send_pr_review),
            patch.object(sdk_cloud_runner, "publish_pr_review") as mock_pub,
        ):
            mock_pub.return_value = PublishResult(
                ok=True,
                message="posted PR review event=COMMENT inline=0",
                review_id=42,
                html_url="https://github.com/o/r/pull/9#pullrequestreview-42",
            )
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="review",
                prompt="Review this PR",
                repo_url="https://github.com/o/r",
                model="cursor-grok-4.5-high",
                prefer_fast=False,
                pr_url="https://github.com/o/r/pull/9",
                delivery="pr_review",
                review_event="COMMENT",
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["delivery"], "pr_review")
        self.assertEqual(
            result["review_url"],
            "https://github.com/o/r/pull/9#pullrequestreview-42",
        )
        self.assertEqual(result["review_id"], "42")
        mock_pub.assert_called_once()
        call_kwargs = mock_pub.call_args.kwargs
        self.assertEqual(call_kwargs["pr_url"], "https://github.com/o/r/pull/9")
        self.assertEqual(call_kwargs["event"], "COMMENT")
        self.assertIn("LGTM", call_kwargs["review_text"])

    def test_pr_review_invalid_for_non_review_kind(self) -> None:
        result = sdk_cloud_runner.run_cloud_sdk(
            kind="plan",
            prompt="x",
            repo_url="https://github.com/o/r",
            model="composer-2.5",
            prefer_fast=True,
            pr_url="https://github.com/o/r/pull/1",
            delivery="pr_review",
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("kind=review", result["result"])

    def test_wait_false(self) -> None:
        fake = _install_fake_sdk()
        with patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake):
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="implement",
                prompt="long job",
                repo_url="https://github.com/o/r",
                model="composer-2.5",
                prefer_fast=True,
                wait=False,
            )
        self.assertEqual(result["status"], "ok")
        self.assertIn("wait=false", result["result"])
        self.assertEqual(result["agent_id"], "bc-test-agent")

    def test_resume_by_agent_id(self) -> None:
        fake = _install_fake_sdk()
        with patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake):
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="plan",
                prompt="continue",
                repo_url="https://github.com/o/r",
                model="composer-2.5",
                prefer_fast=True,
                agent_id="bc-resume-me",
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(_RESUME_CALLS, ["bc-resume-me"])
        self.assertEqual(_CREATE_CALLS, [])

    def test_session_persist(self) -> None:
        fake = _install_fake_sdk()
        with patch.object(sdk_cloud_runner, "_import_sdk", return_value=fake):
            sdk_cloud_runner.run_cloud_sdk(
                kind="plan",
                prompt="first",
                repo_url="https://github.com/o/r",
                model="composer-2.5",
                prefer_fast=True,
            )
        stored = sdk_sessions.load_stored_agent_id(
            workspace="cloud:https://github.com/o/r",
            mode="cloud-plan",
        )
        self.assertEqual(stored, "bc-test-agent")


class EnvelopeCloudTests(unittest.TestCase):
    def test_envelope_includes_cloud_fields(self) -> None:
        from envelope import format_envelope

        text = format_envelope(
            {
                "status": "ok",
                "backend": "sdk-cloud",
                "job_id": "abc",
                "model": "composer-2.5",
                "elapsed_s": 1.2,
                "tools": None,
                "progress_summary": "",
                "result": "done",
                "stderr": "",
                "exit_code": 0,
                "runtime": "cloud",
                "agent_id": "bc-1",
                "repo_url": "https://github.com/o/r",
                "pr_url": "https://github.com/o/r/pull/1",
                "cloud_env": "pool",
                "cloud_env_name": "workers",
            }
        )
        self.assertIn("runtime: cloud", text)
        self.assertIn("agent_id: bc-1", text)
        self.assertIn("pr_url: https://github.com/o/r/pull/1", text)
        self.assertIn("cloud_env: pool name=workers", text)

    def test_envelope_includes_pr_review_fields(self) -> None:
        from envelope import format_envelope

        text = format_envelope(
            {
                "status": "ok",
                "backend": "sdk-cloud",
                "job_id": "abc",
                "model": "cursor-grok-4.5-high",
                "elapsed_s": 28.1,
                "tools": 3,
                "progress_summary": "",
                "result": "done",
                "stderr": "",
                "exit_code": 0,
                "runtime": "cloud",
                "agent_id": "bc-1",
                "repo_url": "https://github.com/o/r",
                "pr_url": "https://github.com/o/r/pull/9",
                "delivery": "pr_review",
                "review_url": "https://github.com/o/r/pull/9#pullrequestreview-42",
                "review_id": "42",
                "usage": {
                    "total_tokens": 12345,
                    "input_tokens": 8000,
                    "output_tokens": 4345,
                },
                "cloud_env": "cloud",
            }
        )
        self.assertIn("delivery: pr_review", text)
        self.assertIn("review_url: https://github.com/o/r/pull/9#pullrequestreview-42", text)
        self.assertIn("review_id: 42", text)
        self.assertIn("usage: total_tokens=12345", text)
        self.assertIn("input_tokens=8000", text)


if __name__ == "__main__":
    unittest.main()
