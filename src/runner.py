"""Backend switch: CLI wrapper or cursor-sdk (auto when API key is set)."""

from __future__ import annotations

import os

from cli_runner import CliRunResult, ProgressCallback, StatusCallback, run_cli
from sdk_runner import run_sdk


def resolve_backend(backend: str | None = None) -> str:
    """Pick backend: explicit arg → env → auto (sdk if CURSOR_API_KEY, else cli).

    Windows auto-stays on ``cli`` (upstream cursor-sdk Bridge select() / WinError
    10038). Force SDK there with ``backend="sdk"`` or ``CURSOR_HEADLESS_BACKEND=sdk``.
    """
    for candidate in (backend, os.environ.get("CURSOR_HEADLESS_BACKEND")):
        selected = (candidate or "").strip().lower()
        if selected in ("cli", "sdk"):
            return selected

    has_key = bool(os.environ.get("CURSOR_API_KEY", "").strip())
    if has_key and os.name != "nt":
        return "sdk"
    return "cli"


def run_cursor(
    *,
    backend: str | None = None,
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
    selected = resolve_backend(backend)
    kwargs = {
        "prompt": prompt,
        "cwd": cwd,
        "mode": mode,
        "model": model,
        "prefer_fast": prefer_fast,
        "force": force,
        "worktree": worktree,
        "skip_preflight": skip_preflight,
        "continue_session": continue_session,
        "timeout": timeout,
        "require_diff": require_diff,
        "job_id": job_id,
        "on_progress": on_progress,
        "on_status": on_status,
    }
    if selected == "sdk":
        return run_sdk(**kwargs)
    return run_cli(**kwargs)
