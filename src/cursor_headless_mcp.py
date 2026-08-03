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
from sdk_cloud_runner import run_cloud_sdk

# Keep in sync with cursor_headless.py DEFAULT_TIMEOUT_SEC.
DEFAULT_TIMEOUT_SEC = float(os.environ.get("CURSOR_HEADLESS_TIMEOUT", "1200"))
DEFAULT_CLOUD_TIMEOUT_SEC = float(
    os.environ.get("CURSOR_HEADLESS_CLOUD_TIMEOUT", str(DEFAULT_TIMEOUT_SEC))
)

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


def _dispatch_cloud(
    *,
    kind: str,
    prompt: str,
    repo_url: str,
    model: str,
    prefer_fast: bool,
    starting_ref: str | None,
    pr_url: str | None,
    auto_create_pr: bool,
    work_on_current_branch: bool,
    skip_reviewer_request: bool,
    cloud_env_type: str,
    cloud_env_name: str | None,
    env_vars: dict[str, str] | None,
    continue_session: bool,
    agent_id: str | None,
    wait: bool,
    timeout: float,
    delivery: str = "findings",
    review_event: str = "COMMENT",
    ctx: Context | None = None,
    progress: bool = True,
) -> str:
    job_id = create_job()
    started = time.monotonic()
    last_info_at = 0.0
    update_job_from_status(
        job_id,
        {
            "phase": f"cloud-{kind}",
            "model": model,
            "message": "starting cloud agent",
            "repo_url": repo_url,
        },
        state="running",
    )

    def on_progress(value: float, message: str) -> None:
        nonlocal last_info_at
        update_job_from_status(
            job_id,
            {
                "phase": f"cloud-{kind}",
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
        update_job_from_status(
            job_id,
            {
                "phase": f"cloud-{kind}",
                "model": model,
                "event": event.get("type"),
                "subtype": event.get("subtype"),
                "elapsed_sec": round(time.monotonic() - started, 1),
            },
            state="running",
        )

    result = run_cloud_sdk(
        kind=kind,
        prompt=prompt,
        repo_url=repo_url,
        model=model,
        prefer_fast=prefer_fast,
        starting_ref=starting_ref,
        pr_url=pr_url,
        auto_create_pr=auto_create_pr,
        work_on_current_branch=work_on_current_branch,
        skip_reviewer_request=skip_reviewer_request,
        cloud_env_type=cloud_env_type,
        cloud_env_name=cloud_env_name,
        env_vars=env_vars,
        continue_session=continue_session,
        agent_id=agent_id,
        wait=wait,
        timeout=timeout,
        delivery=delivery,  # type: ignore[arg-type]
        review_event=review_event,  # type: ignore[arg-type]
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
            "phase": f"cloud-{kind}",
            "model": result.get("model") or model,
            "message": f"finished status={terminal}",
            "tools": result.get("tools"),
            "elapsed_sec": result.get("elapsed_s"),
            "agent_id": result.get("agent_id"),
            "pr_url": result.get("pr_url"),
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


@mcp.tool()
def cursor_cloud_plan(
    prompt: str,
    repo_url: str,
    model: str = "cursor-grok-4.5-high",
    fast: bool = False,
    starting_ref: str = "main",
    pr_url: str | None = None,
    cloud_env_type: str = "cloud",
    cloud_env_name: str | None = None,
    env_vars: dict[str, str] | None = None,
    continue_session: bool = False,
    agent_id: str | None = None,
    wait: bool = True,
    timeout: float = DEFAULT_CLOUD_TIMEOUT_SEC,
    progress: bool = True,
    ctx: Context | None = None,
) -> str:
    """Cloud Cursor plan/explore on a GitHub repo VM (SDK cloud runtime).

    Requires CURSOR_API_KEY. Pass repo_url (https://github.com/org/repo). Optional
    starting_ref / pr_url. cloud_env_type: cloud | pool | machine.
    Default model cursor-grok-4.5-high. Does not edit; use cursor_cloud_implement to write.
    Envelope includes agent_id (bc-…) for resume. On timeout: no result.
    """
    return _dispatch_cloud(
        kind="plan",
        prompt=prompt,
        repo_url=repo_url,
        model=model,
        prefer_fast=fast,
        starting_ref=starting_ref,
        pr_url=pr_url,
        auto_create_pr=False,
        work_on_current_branch=False,
        skip_reviewer_request=False,
        cloud_env_type=cloud_env_type,
        cloud_env_name=cloud_env_name,
        env_vars=env_vars,
        continue_session=continue_session,
        agent_id=agent_id,
        wait=wait,
        timeout=timeout,
        ctx=ctx,
        progress=progress,
    )


@mcp.tool()
def cursor_cloud_review(
    prompt: str,
    repo_url: str,
    model: str = "cursor-grok-4.5-high",
    fast: bool = False,
    starting_ref: str = "main",
    pr_url: str | None = None,
    delivery: str = "findings",
    review_event: str = "COMMENT",
    cloud_env_type: str = "cloud",
    cloud_env_name: str | None = None,
    env_vars: dict[str, str] | None = None,
    continue_session: bool = False,
    agent_id: str | None = None,
    wait: bool = True,
    timeout: float = DEFAULT_CLOUD_TIMEOUT_SEC,
    progress: bool = True,
    ctx: Context | None = None,
) -> str:
    """Cloud Cursor read-only review on a repo/PR VM (SDK cloud runtime).

    Requires CURSOR_API_KEY. Prefer pr_url when reviewing a pull request.

    delivery:
    - findings (default): return review text in the envelope only
    - pr_review: submit a GitHub PR review via host `gh` (requires pr_url).
      Batched review with summary + Files-changed inlines (diff-validated),
      optional multi-line / file-level / suggestion blocks. Needs `gh` auth
      on the MCP host — not the cloud-agent token.

    review_event for delivery=pr_review: COMMENT | REQUEST_CHANGES | APPROVE.
    Does not implement fixes (use cursor_cloud_implement). On timeout: no result.
    """
    return _dispatch_cloud(
        kind="review",
        prompt=prompt,
        repo_url=repo_url,
        model=model,
        prefer_fast=fast,
        starting_ref=starting_ref,
        pr_url=pr_url,
        auto_create_pr=False,
        work_on_current_branch=False,
        skip_reviewer_request=False,
        cloud_env_type=cloud_env_type,
        cloud_env_name=cloud_env_name,
        env_vars=env_vars,
        continue_session=continue_session,
        agent_id=agent_id,
        wait=wait,
        timeout=timeout,
        delivery=delivery,
        review_event=review_event,
        ctx=ctx,
        progress=progress,
    )


@mcp.tool()
def cursor_cloud_implement(
    prompt: str,
    repo_url: str,
    model: str = "composer-2.5",
    fast: bool = False,
    starting_ref: str = "main",
    pr_url: str | None = None,
    auto_create_pr: bool = True,
    work_on_current_branch: bool = False,
    skip_reviewer_request: bool = False,
    cloud_env_type: str = "cloud",
    cloud_env_name: str | None = None,
    env_vars: dict[str, str] | None = None,
    continue_session: bool = False,
    agent_id: str | None = None,
    wait: bool = True,
    timeout: float = DEFAULT_CLOUD_TIMEOUT_SEC,
    progress: bool = True,
    ctx: Context | None = None,
) -> str:
    """Cloud Cursor write-capable implementation on a dedicated VM (SDK cloud).

    Requires CURSOR_API_KEY + repo_url. Defaults auto_create_pr=true. Pick model by
    complexity (composer-2.5 default; escalate to Grok). Optional pool/machine via
    cloud_env_type + cloud_env_name. Set wait=false to detach after start and resume
    later with agent_id. Envelope may include pr_url. On timeout: no result.
    """
    return _dispatch_cloud(
        kind="implement",
        prompt=prompt,
        repo_url=repo_url,
        model=model,
        prefer_fast=fast,
        starting_ref=starting_ref,
        pr_url=pr_url,
        auto_create_pr=auto_create_pr,
        work_on_current_branch=work_on_current_branch,
        skip_reviewer_request=skip_reviewer_request,
        cloud_env_type=cloud_env_type,
        cloud_env_name=cloud_env_name,
        env_vars=env_vars,
        continue_session=continue_session,
        agent_id=agent_id,
        wait=wait,
        timeout=timeout,
        ctx=ctx,
        progress=progress,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
