"""Backend switch: CLI wrapper (default) or cursor-sdk."""

from __future__ import annotations

import os

from cli_runner import CliRunResult, ProgressCallback, StatusCallback, run_cli
from sdk_runner import run_sdk


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
    selected = (backend or os.environ.get("CURSOR_HEADLESS_BACKEND") or "cli").lower()
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
