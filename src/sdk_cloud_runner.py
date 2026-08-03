"""Run Cursor Agent via cursor-sdk cloud runtime (VM / pool / machine)."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Mapping
from typing import Any

import sdk_runner
from cli_runner import CliRunResult, ProgressCallback, StatusCallback
from sdk_runner import (
    _ASK_PREFIX,
    _StreamState,
    _build_model_selection,
    _handle_sdk_message,
    _import_sdk,
    _parse_sdk_model,
    _resolve_status,
)
from pr_review_publish import (
    Delivery,
    ReviewEvent,
    ReviewPublishMeta,
    publish_pr_review,
    review_json_instructions,
)
from sdk_sessions import load_stored_agent_id, save_stored_agent_id

_REVIEW_PREFIX = (
    "Read-only code review: inspect the repository or pull request and report "
    "findings with file/line evidence. Do not edit files or open drive-by "
    "refactors unless explicitly asked.\n\n"
)

_CLOUD_MODES = frozenset({"plan", "review", "implement"})
_DELIVERIES = frozenset({"findings", "pr_review"})


def _normalize_review_event(raw: str | ReviewEvent) -> ReviewEvent:
    value = str(raw or "COMMENT").strip().upper().replace("-", "_")
    if value in {"COMMENT", "REQUEST_CHANGES", "APPROVE"}:
        return value  # type: ignore[return-value]
    if value in {"REQUEST CHANGES", "CHANGES_REQUESTED"}:
        return "REQUEST_CHANGES"
    return "COMMENT"


def _empty_cloud_result(*, job_id: str, model: str, message: str) -> CliRunResult:
    return {
        "status": "error",
        "backend": "sdk-cloud",
        "job_id": job_id,
        "model": model,
        "elapsed_s": 0.0,
        "tools": None,
        "progress_summary": "",
        "result": message,
        "stderr": "",
        "exit_code": 1,
        "runtime": "cloud",
    }


def _session_workspace(repo_url: str) -> str:
    return f"cloud:{repo_url.strip()}"


def _session_mode(kind: str) -> str:
    return f"cloud-{kind}"


def _token_usage_dict(usage: object | None) -> dict[str, int] | None:
    if usage is None:
        return None
    out: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "reasoning_tokens",
    ):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            out[key] = value
    return out or None


def _parse_env_vars(env_vars: Mapping[str, str] | None) -> dict[str, str] | None:
    if not env_vars:
        return None
    cleaned: dict[str, str] = {}
    for key, value in env_vars.items():
        name = str(key).strip()
        if not name or name.startswith("CURSOR_"):
            continue
        cleaned[name] = str(value)
    return cleaned or None


def _extract_pr_url(wait_result: object, run: object) -> str | None:
    for obj in (wait_result, run):
        if obj is None:
            continue
        git = getattr(obj, "git", None)
        if git is None and isinstance(obj, dict):
            git = obj.get("git")
        if git is None:
            continue
        branches = getattr(git, "branches", None)
        if branches is None and isinstance(git, dict):
            branches = git.get("branches")
        if not isinstance(branches, (list, tuple)):
            continue
        for branch in branches:
            pr_url = getattr(branch, "pr_url", None) or getattr(branch, "prUrl", None)
            if pr_url is None and isinstance(branch, dict):
                pr_url = branch.get("pr_url") or branch.get("prUrl")
            if isinstance(pr_url, str) and pr_url.strip():
                return pr_url.strip()
    return None


def _build_cloud_options(
    sdk: object,
    *,
    repo_url: str,
    starting_ref: str | None,
    pr_url: str | None,
    auto_create_pr: bool,
    work_on_current_branch: bool,
    skip_reviewer_request: bool,
    cloud_env_type: str,
    cloud_env_name: str | None,
    env_vars: dict[str, str] | None,
) -> object:
    CloudAgentOptions = getattr(sdk, "CloudAgentOptions", None)
    CloudRepository = getattr(sdk, "CloudRepository", None)
    CloudEnvironment = getattr(sdk, "CloudEnvironment", None)

    env_type = (cloud_env_type or "cloud").strip().lower()
    if env_type not in {"cloud", "pool", "machine"}:
        env_type = "cloud"

    repo_kwargs: dict[str, Any] = {"url": repo_url}
    if starting_ref:
        repo_kwargs["starting_ref"] = starting_ref
    if pr_url:
        repo_kwargs["pr_url"] = pr_url

    if CloudRepository is not None and CloudAgentOptions is not None:
        env_obj = None
        if CloudEnvironment is not None:
            env_obj = CloudEnvironment(type=env_type, name=cloud_env_name)
        else:
            env_obj = {"type": env_type, "name": cloud_env_name}
        return CloudAgentOptions(
            env=env_obj,
            repos=[CloudRepository(**repo_kwargs)],
            work_on_current_branch=work_on_current_branch,
            auto_create_pr=auto_create_pr,
            skip_reviewer_request=skip_reviewer_request,
            env_vars=env_vars,
        )

    return {
        "env": {"type": env_type, "name": cloud_env_name},
        "repos": [repo_kwargs],
        "work_on_current_branch": work_on_current_branch,
        "auto_create_pr": auto_create_pr,
        "skip_reviewer_request": skip_reviewer_request,
        "env_vars": env_vars,
    }


def _mode_for_kind(
    kind: str,
    prompt: str,
    *,
    delivery: Delivery = "findings",
    review_event: ReviewEvent = "COMMENT",
) -> tuple[str, str]:
    """Map cloud tool kind → SDK SendOptions.mode + prompt."""
    if kind == "plan":
        return "plan", prompt
    if kind == "review":
        body = f"{_REVIEW_PREFIX}{prompt}"
        if delivery == "pr_review":
            body = f"{body.rstrip()}\n\n{review_json_instructions(event=review_event)}"
        return "plan", body
    if kind == "implement":
        return "agent", prompt
    # ask-shaped fallback (unused by MCP tools today)
    return "plan", f"{_ASK_PREFIX}{prompt}"


def _open_cloud_agent(
    *,
    Agent: object,
    AgentOptions: object | None,
    model_selection: object,
    api_key: str,
    cloud_options: object,
    continue_session: bool,
    agent_id: str | None,
    repo_url: str,
    kind: str,
    on_progress: ProgressCallback | None,
) -> tuple[object, str, str | None]:
    """Return (agent CM, resume note, resolved agent_id hint)."""
    resume_note = ""
    resume_id = (agent_id or "").strip()
    if continue_session and not resume_id:
        resume_id = os.environ.get("CURSOR_HEADLESS_SDK_AGENT_ID", "").strip()
    if continue_session and not resume_id:
        resume_id = (
            load_stored_agent_id(
                workspace=_session_workspace(repo_url),
                mode=_session_mode(kind),
            )
            or ""
        )

    if resume_id:
        resume = getattr(Agent, "resume", None)
        if callable(resume):
            options: object
            if AgentOptions is not None:
                options = AgentOptions(api_key=api_key)
            else:
                options = {"api_key": api_key}
            try:
                return resume(resume_id, options), "", resume_id
            except Exception as exc:
                resume_note = (
                    f"sdk-cloud: Agent.resume({resume_id!r}) failed ({exc}); "
                    "falling back to Agent.create"
                )
                if on_progress:
                    on_progress(0.02, resume_note)
        else:
            resume_note = "sdk-cloud: Agent.resume unavailable; falling back to Agent.create"

    return (
        Agent.create(
            model=model_selection,
            api_key=api_key,
            cloud=cloud_options,
        ),
        resume_note,
        None,
    )


def run_cloud_sdk(
    *,
    kind: str,
    prompt: str,
    repo_url: str,
    model: str,
    prefer_fast: bool,
    starting_ref: str | None = "main",
    pr_url: str | None = None,
    auto_create_pr: bool = False,
    work_on_current_branch: bool = False,
    skip_reviewer_request: bool = False,
    cloud_env_type: str = "cloud",
    cloud_env_name: str | None = None,
    env_vars: Mapping[str, str] | None = None,
    continue_session: bool = False,
    agent_id: str | None = None,
    wait: bool = True,
    timeout: float = 1200.0,
    delivery: Delivery = "findings",
    review_event: ReviewEvent = "COMMENT",
    job_id: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
) -> CliRunResult:
    """Run a Cursor cloud agent; envelope matches local tools plus cloud fields."""
    resolved_job_id = job_id or uuid.uuid4().hex[:12]
    kind_norm = kind.strip().lower()
    model_spec = _parse_sdk_model(model, prefer_fast)
    resolved_model = model_spec.label
    delivery_norm: Delivery = (delivery or "findings").strip().lower()  # type: ignore[assignment]
    if delivery_norm not in _DELIVERIES:
        delivery_norm = "findings"
    event_norm: ReviewEvent = _normalize_review_event(review_event)

    if kind_norm not in _CLOUD_MODES:
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=f"error: unsupported cloud kind {kind!r} (use plan|review|implement)",
        )

    if delivery_norm == "pr_review" and kind_norm != "review":
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: delivery=pr_review is only valid for kind=review",
        )

    repo = repo_url.strip()
    if not repo:
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: repo_url is required for cloud agents",
        )

    pr = (pr_url or "").strip() or None
    if delivery_norm == "pr_review" and not pr:
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: delivery=pr_review requires pr_url",
        )

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=(
                "error: CURSOR_API_KEY is required for cloud agents. "
                "Set it from Cursor Dashboard → API Keys."
            ),
        )

    sdk = _import_sdk()
    if sdk is None:
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=(
                "error: cursor-sdk is not installed. Install with "
                "`uv pip install cursor-sdk` or use the plugin MCP uv launcher."
            ),
        )

    Agent = getattr(sdk, "Agent", None)
    AgentOptions = getattr(sdk, "AgentOptions", None)
    SendOptions = getattr(sdk, "SendOptions", None)
    CloudSendOptions = getattr(sdk, "CloudSendOptions", None)
    if Agent is None:
        return _empty_cloud_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: cursor-sdk is installed but missing Agent export",
        )

    cleaned_env = _parse_env_vars(env_vars)
    model_selection = _build_model_selection(sdk, model_spec)
    sdk_mode, effective_prompt = _mode_for_kind(
        kind_norm,
        prompt,
        delivery=delivery_norm,
        review_event=event_norm,
    )
    cloud_options = _build_cloud_options(
        sdk,
        repo_url=repo,
        starting_ref=starting_ref.strip() if starting_ref else None,
        pr_url=pr,
        auto_create_pr=auto_create_pr,
        work_on_current_branch=work_on_current_branch,
        skip_reviewer_request=skip_reviewer_request,
        cloud_env_type=cloud_env_type,
        cloud_env_name=cloud_env_name.strip() if cloud_env_name else None,
        env_vars=cleaned_env,
    )

    state = _StreamState(model=resolved_model)
    started = time.monotonic()
    timed_out = False
    stderr = ""
    exit_code = 0
    run_status = "error"
    resolved_agent_id = ""
    resolved_pr_url: str | None = None
    usage_dict: dict[str, int] | None = None
    env_label = (cloud_env_type or "cloud").strip().lower() or "cloud"

    try:
        agent_cm, resume_note, _hint = _open_cloud_agent(
            Agent=Agent,
            AgentOptions=AgentOptions,
            model_selection=model_selection,
            api_key=api_key,
            cloud_options=cloud_options,
            continue_session=continue_session or bool((agent_id or "").strip()),
            agent_id=agent_id,
            repo_url=repo,
            kind=kind_norm,
            on_progress=on_progress,
        )
        if resume_note:
            stderr = resume_note
            state.summary_lines.append(resume_note)

        with agent_cm as agent:
            raw_id = getattr(agent, "agent_id", None)
            if isinstance(raw_id, str) and raw_id.strip():
                resolved_agent_id = raw_id.strip()
                save_stored_agent_id(
                    workspace=_session_workspace(repo),
                    mode=_session_mode(kind_norm),
                    agent_id=resolved_agent_id,
                )
                if on_progress:
                    on_progress(0.05, f"cloud agent_id={resolved_agent_id}")

            send_kwargs: dict[str, object] = {
                "mode": sdk_mode,
                "model": model_selection,
            }
            if cleaned_env is not None and CloudSendOptions is not None:
                # Per-send env only when caller passed env_vars (also set on create).
                send_kwargs["cloud"] = CloudSendOptions(env_vars=cleaned_env)
            elif cleaned_env is not None:
                send_kwargs["cloud"] = {"env_vars": cleaned_env}

            if SendOptions is not None:
                run = agent.send(effective_prompt, SendOptions(**send_kwargs))
            else:
                run = agent.send(effective_prompt, send_kwargs)

            with sdk_runner._active_lock:
                sdk_runner._active_runs[resolved_job_id] = run

            try:
                if not wait:
                    run_status = "finished"
                    state.result_text = (
                        f"cloud agent started (wait=false)\n"
                        f"agent_id: {resolved_agent_id or '(unknown)'}\n"
                        f"Resume with continue_session=true or agent_id=…"
                    )
                else:
                    messages = getattr(run, "messages", None)
                    if callable(messages):
                        for message in messages():
                            _handle_sdk_message(
                                state,
                                message,
                                on_progress=on_progress,
                                on_status=on_status,
                            )
                            if time.monotonic() - started > timeout:
                                timed_out = True
                                cancel = getattr(run, "cancel", None)
                                if callable(cancel):
                                    cancel()
                                break

                    wait_fn = getattr(run, "wait", None)
                    wait_result: object | None = None
                    if callable(wait_fn):
                        wait_result = wait_fn()
                        run_status = str(
                            getattr(wait_result, "status", "") or getattr(run, "status", "")
                        )
                        result_obj = getattr(wait_result, "result", None)
                        if isinstance(result_obj, str) and result_obj.strip():
                            state.result_text = result_obj
                        else:
                            text_fn = getattr(run, "text", None)
                            if callable(text_fn):
                                state.result_text = str(text_fn())
                        resolved = getattr(wait_result, "model", None)
                        model_id = (
                            getattr(resolved, "id", None) if resolved is not None else None
                        )
                        if isinstance(model_id, str) and model_id:
                            state.model = model_id
                        usage_dict = _token_usage_dict(
                            getattr(wait_result, "usage", None)
                        )
                        if usage_dict is None:
                            usage_dict = _token_usage_dict(getattr(run, "usage", None))
                        resolved_pr_url = _extract_pr_url(wait_result, run)
                    else:
                        run_status = str(getattr(run, "status", "error"))
                        text_fn = getattr(run, "text", None)
                        if callable(text_fn):
                            state.result_text = str(text_fn())
                        usage_dict = _token_usage_dict(getattr(run, "usage", None))
            finally:
                with sdk_runner._active_lock:
                    sdk_runner._active_runs.pop(resolved_job_id, None)

    except Exception as exc:
        failed = _empty_cloud_result(
            job_id=resolved_job_id,
            model=state.model or resolved_model,
            message=f"error: cloud SDK run failed: {exc}",
        )
        failed["elapsed_s"] = round(time.monotonic() - started, 2)
        if state.summary_lines:
            failed["progress_summary"] = "\n".join(state.summary_lines)
        if resolved_agent_id:
            failed["agent_id"] = resolved_agent_id
        failed["repo_url"] = repo
        failed["cloud_env"] = env_label
        return failed

    elapsed_s = round(time.monotonic() - started, 2)
    result_text = state.result_text
    status = _resolve_status(
        timed_out=timed_out, run_status=run_status, result_text=result_text
    )
    if status == "timeout":
        timeout_msg = (
            f"error: timed out after {timeout:g}s — treat as no result; "
            f"retry with a narrower prompt, raise timeout, or resume agent_id="
            f"{resolved_agent_id or '…'}"
        )
        result_text = f"{timeout_msg}\n{result_text}".strip() if result_text else timeout_msg
        exit_code = 124
    elif status == "error":
        exit_code = 1

    if not resolved_pr_url and pr:
        resolved_pr_url = pr

    out: CliRunResult = {
        "status": status,
        "backend": "sdk-cloud",
        "job_id": resolved_job_id,
        "model": state.model or resolved_model,
        "elapsed_s": elapsed_s,
        "tools": state.tools if state.tools > 0 else None,
        "progress_summary": "\n".join(state.summary_lines),
        "result": result_text,
        "stderr": stderr,
        "exit_code": exit_code,
        "runtime": "cloud",
        "repo_url": repo,
        "cloud_env": env_label,
        "delivery": delivery_norm,
    }
    if resolved_agent_id:
        out["agent_id"] = resolved_agent_id
    if resolved_pr_url:
        out["pr_url"] = resolved_pr_url
    if cloud_env_name:
        out["cloud_env_name"] = cloud_env_name.strip()
    if usage_dict:
        out["usage"] = usage_dict

    if (
        status == "ok"
        and kind_norm == "review"
        and delivery_norm == "pr_review"
        and resolved_pr_url
        and wait
    ):
        if on_progress:
            on_progress(0.97, f"publishing GitHub PR review on {resolved_pr_url}")
        published = publish_pr_review(
            pr_url=resolved_pr_url,
            review_text=result_text,
            event=event_norm,
            meta=ReviewPublishMeta(
                model=state.model or resolved_model,
                elapsed_s=elapsed_s,
                usage=usage_dict,
                agent_id=resolved_agent_id or None,
                tools=state.tools if state.tools > 0 else None,
                cloud_env=env_label,
                backend="sdk-cloud",
                job_id=resolved_job_id,
            ),
        )
        out["delivery"] = "pr_review"
        if published.ok:
            note = published.message
            if published.html_url:
                out["review_url"] = published.html_url
                note = f"{note}\nreview_url: {published.html_url}"
            if published.review_id is not None:
                out["review_id"] = str(published.review_id)
            out["result"] = f"{result_text.rstrip()}\n\n--- github pr review ---\n{note}\n"
            state.summary_lines.append(note)
            out["progress_summary"] = "\n".join(state.summary_lines)
        else:
            out["status"] = "error"
            out["exit_code"] = 1
            out["result"] = (
                f"{result_text.rstrip()}\n\n--- github pr review ---\n{published.message}\n"
            )
            out["stderr"] = (
                f"{stderr}\n{published.message}".strip() if stderr else published.message
            )

    return out
