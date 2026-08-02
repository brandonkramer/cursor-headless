#!/usr/bin/env python3
"""Thin MCP facade over cursor_headless.py — native tools, stream-json + progress."""

from __future__ import annotations

import os
import sys
import time

from mcp.server.fastmcp import Context, FastMCP

from envelope import format_envelope
from jobs import JobState, create_job, register_status_tool, update_job_from_status
from runner import run_cursor

# Keep in sync with cursor_headless.py DEFAULT_TIMEOUT_SEC.
DEFAULT_TIMEOUT_SEC = float(os.environ.get("CURSOR_HEADLESS_TIMEOUT", "1200"))

mcp = FastMCP("cursor-headless")
register_status_tool(mcp)

_INFO_INTERVAL_SEC = 5.0


def _configure_utf8_stdio() -> None:
    """Avoid Windows CP-1252 UnicodeEncodeError when tool results contain non-ASCII."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError, AttributeError):
                pass


_configure_utf8_stdio()


def _dispatch(
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
    backend: str | None,
    ctx: Context | None,
    progress: bool,
) -> str:
    job_id = create_job()
    started = time.monotonic()
    last_info_at = 0.0
    update_job_from_status(
        job_id,
        {"phase": mode, "model": model, "message": "starting"},
        state="running",
    )

    def on_progress(value: float, message: str) -> None:
        nonlocal last_info_at
        update_job_from_status(
            job_id,
            {
                "phase": mode,
                "model": model,
                "message": message,
                "progress": value,
                "elapsed_sec": round(time.monotonic() - started, 1),
            },
            state="running",
        )
        if not ctx or not progress:
            return
        ctx.report_progress(value, total=None, message=message)
        now = time.monotonic()
        if now - last_info_at >= _INFO_INTERVAL_SEC:
            ctx.info(message)
            last_info_at = now

    def on_status(event: dict[str, object]) -> None:
        etype = event.get("type")
        subtype = event.get("subtype")
        update_job_from_status(
            job_id,
            {
                "phase": mode,
                "model": model,
                "event": etype,
                "subtype": subtype,
                "elapsed_sec": round(time.monotonic() - started, 1),
            },
            state="running",
        )

    result = run_cursor(
        backend=backend,
        prompt=prompt,
        cwd=cwd,
        mode=mode,
        model=model,
        prefer_fast=prefer_fast,
        force=force,
        worktree=worktree,
        skip_preflight=skip_preflight,
        continue_session=continue_session,
        timeout=timeout,
        require_diff=require_diff,
        job_id=job_id,
        on_progress=on_progress if progress else None,
        on_status=on_status if progress else None,
    )
    terminal: str = result["status"]
    job_state: JobState = (
        "timeout" if terminal == "timeout" else "done" if terminal == "ok" else "error"
    )
    update_job_from_status(
        job_id,
        {
            "phase": mode,
            "model": result.get("model") or model,
            "message": f"finished status={terminal}",
            "tools": result.get("tools"),
            "elapsed_sec": result.get("elapsed_s"),
        },
        state=job_state,
    )
    return format_envelope(result)


@mcp.tool()
def cursor_ask(
    prompt: str,
    cwd: str = ".",
    model: str = "cursor-grok-4.5-high",
    fast: bool = False,
    skip_preflight: bool = True,
    continue_session: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    backend: str | None = None,
    progress: bool = True,
    ctx: Context | None = None,
) -> str:
    """Read-only Cursor ask (--mode ask).

    Default model cursor-grok-4.5-high; caller picks low|medium|high and whether to use Fast
    (fast=true or *-fast id). Pass composer-2.5 for cheaper mechanical Q&A.

    Parent/orchestrator owns timeout (default 1200s, or CURSOR_HEADLESS_TIMEOUT).
    Raise for broad multi-app maps; lower for tiny one-shot Q&A. On timeout: no result —
    narrow the prompt or raise timeout and retry.
    """
    return _dispatch(
        prompt=prompt,
        cwd=cwd,
        mode="ask",
        model=model,
        prefer_fast=fast,
        force=False,
        worktree=None,
        skip_preflight=skip_preflight,
        continue_session=continue_session,
        timeout=timeout,
        require_diff=False,
        backend=backend,
        ctx=ctx,
        progress=progress,
    )


@mcp.tool()
def cursor_plan(
    prompt: str,
    cwd: str = ".",
    model: str = "cursor-grok-4.5-high",
    fast: bool = False,
    skip_preflight: bool = True,
    continue_session: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    backend: str | None = None,
    progress: bool = True,
    ctx: Context | None = None,
) -> str:
    """Read-only Cursor plan/explore (--mode plan).

    Default model cursor-grok-4.5-high; caller picks low|medium|high and whether to use Fast
    (fast=true or *-fast id). Pass composer-2.5 for cheaper plans.

    Parent/orchestrator owns timeout (default 1200s, or CURSOR_HEADLESS_TIMEOUT).
    Broad duplicate/consumer inventory often needs 1200–1800 or a narrower path slice.
    On timeout: treat as no result — do not invent findings; narrow or raise timeout.
    """
    return _dispatch(
        prompt=prompt,
        cwd=cwd,
        mode="plan",
        model=model,
        prefer_fast=fast,
        force=False,
        worktree=None,
        skip_preflight=skip_preflight,
        continue_session=continue_session,
        timeout=timeout,
        require_diff=False,
        backend=backend,
        ctx=ctx,
        progress=progress,
    )


@mcp.tool()
def cursor_implement(
    prompt: str,
    cwd: str = ".",
    model: str = "composer-2.5",
    fast: bool = False,
    worktree: str | None = None,
    force: bool = True,
    skip_preflight: bool = True,
    continue_session: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    require_diff: bool = False,
    backend: str | None = None,
    progress: bool = True,
    ctx: Context | None = None,
) -> str:
    """Write-capable Cursor implementation (--mode default).

    Caller picks the model by task complexity (see skill):
    - simple/mechanical → composer-2.5 (default); set fast=true or model=composer-2.5-fast when speed matters
    - light → cursor-grok-4.5-low (+ Fast optional)
    - medium → cursor-grok-4.5-medium (+ Fast optional)
    - hard/ambiguous/cross-cutting → cursor-grok-4.5-high (+ Fast optional)

    Set worktree for isolation; force defaults true. `fast` defaults false — opt in to upgrade to *-fast.
    Set require_diff=true to fail when Cursor claims success but git status is clean.

    Parent/orchestrator owns timeout (default 1200s, or CURSOR_HEADLESS_TIMEOUT).
    Raise for large write slices; prefer smaller slices when possible. On timeout: no result.
    """
    return _dispatch(
        prompt=prompt,
        cwd=cwd,
        mode="default",
        model=model,
        prefer_fast=fast,
        force=force,
        worktree=worktree,
        skip_preflight=skip_preflight,
        continue_session=continue_session,
        timeout=timeout,
        require_diff=require_diff,
        backend=backend,
        ctx=ctx,
        progress=progress,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
