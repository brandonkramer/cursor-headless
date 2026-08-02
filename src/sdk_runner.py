"""Run Cursor Agent via cursor-sdk (local runtime) with streaming progress."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cli_runner import CliRunResult, ProgressCallback, StatusCallback
from sdk_sessions import load_stored_agent_id, save_stored_agent_id

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


@dataclass(frozen=True)
class _SdkModelSpec:
    """SDK model id + params (Fast is a param, not a `*-fast` suffix)."""

    model_id: str
    fast: bool
    effort: str | None = None

    @property
    def label(self) -> str:
        bits = [self.model_id]
        if self.effort:
            bits.append(f"effort={self.effort}")
        bits.append(f"fast={'true' if self.fast else 'false'}")
        return " ".join(bits)


_GROK_MODEL_RE = re.compile(
    r"^(?:cursor-)?grok-4\.5(?:-(low|medium|high))?$",
    re.IGNORECASE,
)


def _parse_sdk_model(model: str, prefer_fast: bool) -> _SdkModelSpec:
    """Map CLI-style ids (`composer-2.5-fast`, `cursor-grok-4.5-high`) to SDK params."""
    raw = model.strip()
    fast = prefer_fast or raw.endswith("-fast")
    if raw.endswith("-fast"):
        raw = raw[: -len("-fast")]

    effort: str | None = None
    model_id = raw
    match = _GROK_MODEL_RE.fullmatch(raw)
    if match:
        model_id = "grok-4.5"
        effort = (match.group(1) or "high").lower()

    return _SdkModelSpec(model_id=model_id, fast=fast, effort=effort)


def _build_model_selection(sdk: object, spec: _SdkModelSpec) -> object:
    """Build ModelSelection (or dict fallback) with fast/effort params."""
    params: list[dict[str, str]] = []
    if spec.effort:
        params.append({"id": "effort", "value": spec.effort})
    params.append({"id": "fast", "value": "true" if spec.fast else "false"})

    ModelSelection = getattr(sdk, "ModelSelection", None)
    ModelParameterValue = getattr(sdk, "ModelParameterValue", None)
    if ModelSelection is not None and ModelParameterValue is not None:
        return ModelSelection(
            id=spec.model_id,
            params=[
                ModelParameterValue(id=item["id"], value=item["value"]) for item in params
            ],
        )
    return {"id": spec.model_id, "params": params}


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


_WORKTREE_ROOT_DIR = ".cursor-headless/worktrees"
_FORCE_PROMPT_SUFFIX = (
    "\n\nAuto-approve shell commands and file edits unless explicitly denied "
    "(equivalent to cursor-agent --force)."
)


def _sanitize_worktree_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", name.strip()).strip("-")
    return cleaned or "worktree"


def _git_toplevel(cwd: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    top = (proc.stdout or "").strip()
    return top or None


def _is_git_worktree(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(path),
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


def _prepare_worktree_cwd(
    repo_cwd: str,
    worktree: str | None,
    *,
    job_id: str,
) -> tuple[str, str | None]:
    """Create or reuse a git worktree; return (effective_cwd, error_message).

    Worktrees live under ``<git-root>/.cursor-headless/worktrees/<name>`` and are
    left on disk after the run (no automatic cleanup).
    """
    if worktree is None:
        return repo_cwd, None

    git_root = _git_toplevel(repo_cwd)
    if git_root is None:
        return repo_cwd, (
            "error: --worktree requires a git repository (git rev-parse --show-toplevel failed)"
        )

    label = worktree if worktree else f"cursor-headless-{job_id}"
    name = _sanitize_worktree_name(label)
    worktree_path = Path(git_root) / _WORKTREE_ROOT_DIR / name
    if _is_git_worktree(worktree_path):
        return str(worktree_path.resolve()), None

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    branch = f"cursor-headless/{name}"
    cmd = ["git", "worktree", "add", "-B", branch, str(worktree_path), "HEAD"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=git_root,
            capture_output=True,
            check=False,
            timeout=120,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return repo_cwd, f"error: git worktree add failed: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return repo_cwd, f"error: git worktree add failed ({' '.join(cmd)}): {detail}"

    return str(worktree_path.resolve()), None


def _mode_mapping(mode: str, prompt: str, *, force: bool, force_via_sdk: bool) -> tuple[str, str]:
    """Map CLI modes to SDK SendOptions.mode + prompt adjustments."""
    extra = prompt
    if force and mode == "default" and not force_via_sdk:
        extra = f"{extra.rstrip()}{_FORCE_PROMPT_SUFFIX}"
    if mode == "ask":
        return "plan", f"{_ASK_PREFIX}{extra}"
    if mode == "plan":
        return "plan", extra
    return "agent", extra


def _build_send_options(
    sdk: object,
    *,
    sdk_mode: str,
    force: bool,
    mode: str,
    model_selection: object,
) -> dict[str, object]:
    send_kwargs: dict[str, object] = {"mode": sdk_mode, "model": model_selection}
    if not force or mode != "default":
        return send_kwargs

    LocalSendOptions = getattr(sdk, "LocalSendOptions", None)
    if LocalSendOptions is not None:
        send_kwargs["local"] = LocalSendOptions(force=True)
    else:
        send_kwargs["local"] = {"force": True}
    return send_kwargs


def _resume_options(api_key: str, AgentOptions: object | None) -> object:
    if AgentOptions is not None:
        return AgentOptions(api_key=api_key)
    return {"api_key": api_key}


def _open_agent(
    *,
    Agent: object,
    LocalAgentOptions: object,
    AgentOptions: object | None,
    continue_session: bool,
    workspace: str,
    mode: str,
    model_selection: object,
    api_key: str,
    agent_cwd: str,
    on_progress: ProgressCallback | None,
) -> tuple[object, str]:
    """Return (agent context manager, resume fallback note for stderr/progress)."""
    resume_note = ""
    if continue_session:
        agent_id = os.environ.get("CURSOR_HEADLESS_SDK_AGENT_ID", "").strip()
        if not agent_id:
            agent_id = load_stored_agent_id(workspace=workspace, mode=mode) or ""
        if agent_id:
            resume = getattr(Agent, "resume", None)
            if callable(resume):
                try:
                    return resume(agent_id, _resume_options(api_key, AgentOptions)), ""
                except Exception as exc:
                    resume_note = (
                        f"sdk: Agent.resume({agent_id!r}) failed ({exc}); "
                        "falling back to Agent.create"
                    )
            else:
                resume_note = "sdk: Agent.resume unavailable; falling back to Agent.create"
            if resume_note and on_progress:
                on_progress(0.02, resume_note)

    return Agent.create(
        model=model_selection,
        api_key=api_key,
        local=LocalAgentOptions(cwd=agent_cwd),
    ), resume_note


def _persist_agent_id(*, agent: object, workspace: str, mode: str) -> None:
    raw_id = getattr(agent, "agent_id", None)
    if isinstance(raw_id, str) and raw_id.strip():
        save_stored_agent_id(workspace=workspace, mode=mode, agent_id=raw_id)


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
    model_spec = _parse_sdk_model(model, prefer_fast)
    resolved_model = model_spec.label

    if force and mode != "default":
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: force is only allowed with mode default",
        )

    if skip_preflight:
        pass  # SDK auth is validated at Agent.create / send time.

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=(
                "error: CURSOR_API_KEY is required for backend=sdk. "
                "Set it from Cursor Dashboard → API Keys, or use backend=cli."
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
    AgentOptions = getattr(sdk, "AgentOptions", None)
    SendOptions = getattr(sdk, "SendOptions", None)
    if Agent is None or LocalAgentOptions is None:
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message="error: cursor-sdk is installed but missing Agent/LocalAgentOptions exports",
        )

    model_selection = _build_model_selection(sdk, model_spec)

    workspace = str(Path(cwd).resolve())
    worktree_cwd, worktree_err = _prepare_worktree_cwd(
        workspace,
        worktree,
        job_id=resolved_job_id,
    )
    if worktree_err:
        return _empty_result(
            job_id=resolved_job_id,
            model=resolved_model,
            message=worktree_err,
        )

    LocalSendOptions = getattr(sdk, "LocalSendOptions", None)
    force_via_sdk = force and mode == "default" and LocalSendOptions is not None
    sdk_mode, effective_prompt = _mode_mapping(
        mode,
        prompt,
        force=force,
        force_via_sdk=force_via_sdk,
    )
    state = _StreamState(model=resolved_model)
    started = time.monotonic()
    timed_out = False
    stderr = ""
    exit_code = 0
    run_status = "error"

    try:
        agent_cm, resume_note = _open_agent(
            Agent=Agent,
            LocalAgentOptions=LocalAgentOptions,
            AgentOptions=AgentOptions,
            continue_session=continue_session,
            workspace=workspace,
            mode=mode,
            model_selection=model_selection,
            api_key=api_key,
            agent_cwd=worktree_cwd,
            on_progress=on_progress,
        )
        if resume_note:
            stderr = resume_note
            state.summary_lines.append(resume_note)

        with agent_cm as agent:
            send_kwargs = _build_send_options(
                sdk,
                sdk_mode=sdk_mode,
                force=force,
                mode=mode,
                model_selection=model_selection,
            )
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

            _persist_agent_id(agent=agent, workspace=workspace, mode=mode)

    except Exception as exc:
        return _empty_result(
            job_id=resolved_job_id,
            model=state.model or resolved_model,
            message=f"error: SDK run failed: {exc}",
        )

    elapsed_s = round(time.monotonic() - started, 2)
    result_text = state.result_text

    if mode == "default" and require_diff:
        evidence, has_diff = _git_evidence(worktree_cwd)
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
