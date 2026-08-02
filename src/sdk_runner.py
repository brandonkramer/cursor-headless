"""Run Cursor Agent via cursor-sdk (local runtime) with streaming progress."""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cli_runner import CliRunResult, ProgressCallback, StatusCallback

_ASK_PREFIX = (
    "Read-only ask mode: answer the question without editing files, "
    "running shell commands, or making tree changes.\n\n"
)

_active_runs: dict[str, object] = {}
_active_lock = threading.Lock()


@dataclass
class _StreamState:
    tools: int = 0
    summary_lines: list[str] = field(default_factory=list)
    result_text: str = ""
    model: str = ""
    progress: float = 0.0


def _import_sdk() -> object | None:
    try:
        import cursor_sdk  # noqa: PLC0415

        return cursor_sdk
    except ImportError:
        return None


def _resolve_model(model: str, prefer_fast: bool) -> str:
    resolved = model
    if prefer_fast and not resolved.endswith("-fast"):
        resolved = f"{resolved}-fast"
    return resolved


def _empty_result(*, job_id: str, model: str, message: str) -> CliRunResult:
    return {
        "status": "error",
        "backend": "sdk",
        "job_id": job_id,
        "model": model,
        "elapsed_s": 0.0,
        "tools": None,
        "progress_summary": "",
        "result": message,
        "stderr": "",
        "exit_code": 1,
    }


def _resolve_status(*, timed_out: bool, run_status: str, result_text: str) -> str:
    if timed_out or run_status == "expired":
        return "timeout"
    if run_status in ("cancelled",):
        return "timeout" if timed_out else "error"
    if run_status == "error":
        return "error"
    if run_status == "finished":
        return "ok" if result_text.strip() else "error"
    return "error"


def _message_type(message: object) -> str:
    msg_type = getattr(message, "type", None)
    if isinstance(msg_type, str):
        return msg_type
    if isinstance(message, dict):
        raw = message.get("type")
        if isinstance(raw, str):
            return raw
    return ""


def _assistant_snippet(message: object) -> str:
    inner = getattr(message, "message", None)
    if inner is None and isinstance(message, dict):
        inner = message.get("message")
    content = getattr(inner, "content", None) if inner is not None else None
    if content is None and isinstance(inner, dict):
        content = inner.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if block_type is None and isinstance(block, dict):
            block_type = block.get("type")
            text = block.get("text")
        if block_type == "text" and isinstance(text, str) and text.strip():
            parts.append(text.strip())
    joined = " ".join(parts)
    if len(joined) > 120:
        return joined[:117] + "..."
    return joined


def _tool_label(message: object) -> str:
    name = getattr(message, "name", None)
    if not isinstance(name, str) or not name:
        if isinstance(message, dict):
            raw = message.get("name")
            name = raw if isinstance(raw, str) else "tool"
        else:
            name = "tool"
    args = getattr(message, "args", None)
    detail = ""
    if isinstance(args, dict):
        for key in ("path", "command", "pattern"):
            raw = args.get(key)
            if isinstance(raw, str) and raw:
                detail = raw
                break
    if detail:
        return f"{name}: {detail}"
    return name


def _status_dict(message: object) -> dict[str, object]:
    if isinstance(message, dict):
        return dict(message)
    payload: dict[str, object] = {"type": _message_type(message)}
    for key in ("name", "status", "subtype", "agent_id", "run_id"):
        value = getattr(message, key, None)
        if value is not None:
            payload[key] = value
    return payload


def _handle_sdk_message(
    state: _StreamState,
    message: object,
    *,
    on_progress: ProgressCallback | None,
    on_status: StatusCallback | None,
) -> None:
    msg_type = _message_type(message)

    if msg_type == "system":
        subtype = getattr(message, "subtype", None)
        if subtype is None and isinstance(message, dict):
            subtype = message.get("subtype")
        if subtype == "init":
            raw_model = getattr(message, "model", None)
            if raw_model is None and isinstance(message, dict):
                raw_model = message.get("model")
            if isinstance(raw_model, str) and raw_model:
                state.model = raw_model
            state.progress = max(state.progress, 0.05)
            line = f"init model={state.model or 'unknown'}"
            state.summary_lines.append(line)
            if on_progress:
                on_progress(state.progress, line)
        if on_status:
            on_status(_status_dict(message))
        return

    if msg_type == "assistant":
        snippet = _assistant_snippet(message)
        if snippet:
            state.progress = min(0.92, max(state.progress, 0.08))
            line = f"assistant: {snippet}"
            state.summary_lines.append(line)
            if on_progress:
                on_progress(state.progress, line)
        if on_status:
            on_status(_status_dict(message))
        return

    if msg_type == "tool_call":
        state.tools += 1
        state.progress = min(0.95, 0.1 + state.tools * 0.08)
        line = f"tool #{state.tools} {_tool_label(message)}"
        state.summary_lines.append(line)
        if on_progress:
            on_progress(state.progress, line)
        if on_status:
            on_status(_status_dict(message))
        return

    if on_status:
        on_status(_status_dict(message))


def _git_evidence(cwd: str) -> tuple[str, bool]:
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        ds = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"\n--- sdk git evidence ---\n(unavailable: {exc})\n", False

    porcelain = (st.stdout or "").rstrip()
    diffstat = (ds.stdout or "").rstrip()
    has_diff = bool(porcelain.strip())
    block = (
        "\n--- sdk git evidence ---\n"
        f"git status --porcelain:\n{porcelain if porcelain else '(clean)'}\n"
        f"git diff --stat HEAD:\n{diffstat if diffstat else '(empty)'}\n"
    )
    return block, has_diff


def _mode_mapping(mode: str, prompt: str, *, worktree: str | None) -> tuple[str, str | None]:
    """Map CLI modes to SDK SendOptions.mode + prompt adjustments."""
    extra = prompt
    if worktree is not None:
        label = worktree or "(isolated worktree)"
        extra = f"Use an isolated git worktree named {label!r} for edits.\n\n{extra}"
    if mode == "ask":
        return "plan", f"{_ASK_PREFIX}{extra}"
    if mode == "plan":
        return "plan", extra
    return "agent", extra


def cancel_sdk_run(job_id: str) -> bool:
    """Request cancellation for an in-flight SDK run (best-effort)."""
    with _active_lock:
        handle = _active_runs.get(job_id)
    if handle is None:
        return False
    cancel = getattr(handle, "cancel", None)
    if not callable(cancel):
        return False
    try:
        cancel()
        return True
    except Exception:
        return False


def run_sdk(
    *,
    prompt: str,
    cwd: str,
    mode: str,
    model: str,
    prefer_fast: bool,
    force: bool,
    worktree: str | None,
    skip_preflight: bool,
    continue_session: bool,
    timeout: float,
    require_diff: bool,
    job_id: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
) -> CliRunResult:
    """Run Cursor via cursor-sdk local agent; return the same envelope as run_cli."""
    resolved_job_id = job_id or uuid.uuid4().hex[:12]
    resolved_model = _resolve_model(model, prefer_fast)

    if force and mode != "default":
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: force is only allowed with mode default",
        )

    if continue_session:
        # SDK resume is agent-scoped; headless MCP creates ephemeral agents per call.
        pass

    if skip_preflight:
        pass  # SDK auth is validated at Agent.create / send time.

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=(
                "error: CURSOR_API_KEY is required for CURSOR_HEADLESS_BACKEND=sdk. "
                "Set it from Cursor Dashboard → API Keys, or use backend=cli (default)."
            ),
        )

    sdk = _import_sdk()
    if sdk is None:
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=(
                "error: cursor-sdk is not installed. Install with "
                "`uv pip install cursor-sdk` or `pip install cursor-sdk`, "
                "or use backend=cli (default)."
            ),
        )

    Agent = getattr(sdk, "Agent", None)
    LocalAgentOptions = getattr(sdk, "LocalAgentOptions", None)
    SendOptions = getattr(sdk, "SendOptions", None)
    if Agent is None or LocalAgentOptions is None:
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: cursor-sdk is installed but missing Agent/LocalAgentOptions exports",
        )

    sdk_mode, effective_prompt = _mode_mapping(mode, prompt, worktree=worktree)
    workspace = str(Path(cwd).resolve())
    state = _StreamState(model=resolved_model)
    started = time.monotonic()
    timed_out = False
    stderr = ""
    exit_code = 0
    run_status = "error"

    try:
        with Agent.create(
            model=resolved_model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=workspace),
        ) as agent:
            send_kwargs: dict[str, object] = {"mode": sdk_mode}
            if SendOptions is not None:
                run = agent.send(effective_prompt, SendOptions(**send_kwargs))
            else:
                run = agent.send(effective_prompt, send_kwargs)

            with _active_lock:
                _active_runs[resolved_job_id] = run

            try:
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

                wait = getattr(run, "wait", None)
                if callable(wait):
                    wait_result = wait()
                    run_status = str(getattr(wait_result, "status", "") or getattr(run, "status", ""))
                    result_obj = getattr(wait_result, "result", None)
                    if isinstance(result_obj, str) and result_obj.strip():
                        state.result_text = result_obj
                    resolved = getattr(wait_result, "model", None)
                    model_id = getattr(resolved, "id", None) if resolved is not None else None
                    if isinstance(model_id, str) and model_id:
                        state.model = model_id
                else:
                    run_status = str(getattr(run, "status", "error"))
                    text_fn = getattr(run, "text", None)
                    if callable(text_fn):
                        state.result_text = str(text_fn())
            finally:
                with _active_lock:
                    _active_runs.pop(resolved_job_id, None)

    except Exception as exc:
        return _empty_result(
            job_id=resolved_job_id,
            model=state.model or resolved_model,
            message=f"error: SDK run failed: {exc}",
        )

    elapsed_s = round(time.monotonic() - started, 2)
    result_text = state.result_text

    if mode == "default" and require_diff:
        evidence, has_diff = _git_evidence(workspace)
        result_text = f"{result_text.rstrip()}\n{evidence}".strip() if result_text else evidence.strip()
        if not has_diff and run_status == "finished" and not timed_out:
            run_status = "error"
            exit_code = 2
            result_text = (
                f"{result_text}\n"
                "error: require_diff set but git status --porcelain is clean "
                "(SDK run claimed success with no tree changes)"
            ).strip()

    status = _resolve_status(timed_out=timed_out, run_status=run_status, result_text=result_text)
    if status == "timeout":
        timeout_msg = (
            f"error: timed out after {timeout:g}s — treat as no result; "
            f"retry with a narrower prompt or pass timeout=<higher seconds>"
        )
        result_text = f"{timeout_msg}\n{result_text}".strip() if result_text else timeout_msg
        exit_code = 124
    elif status == "error" and exit_code == 0:
        exit_code = 1

    return {
        "status": status,
        "backend": "sdk",
        "job_id": resolved_job_id,
        "model": state.model or resolved_model,
        "elapsed_s": elapsed_s,
        "tools": state.tools if state.tools > 0 else None,
        "progress_summary": "\n".join(state.summary_lines),
        "result": result_text,
        "stderr": stderr,
        "exit_code": exit_code,
    }
