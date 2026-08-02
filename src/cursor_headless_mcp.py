#!/usr/bin/env python3
"""Thin MCP facade over cursor_headless.py — native tools, same fast path."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PLUGIN_ROOT / "skills" / "cursor-headless" / "scripts" / "cursor_headless.py"

# Keep in sync with cursor_headless.py DEFAULT_TIMEOUT_SEC.
DEFAULT_TIMEOUT_SEC = float(os.environ.get("CURSOR_HEADLESS_TIMEOUT", "1200"))

mcp = FastMCP("cursor-headless")

_SUBPROCESS_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


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


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


_configure_utf8_stdio()


def _run_wrapper(
    *,
    prompt: str,
    cwd: str,
    mode: str,
    model: str,
    prefer_fast: bool,
    force: bool,
    worktree: str | None,
    skip_preflight: bool,
    output_format: str,
    continue_session: bool,
    timeout: float,
    require_diff: bool,
) -> str:
    if not WRAPPER.is_file():
        return f"error: wrapper missing at {WRAPPER}"

    # Never put multiline prompts on argv (Windows .cmd + Python argv mangling).
    prompt_path: str | None = None
    try:
        fd, prompt_path = tempfile.mkstemp(prefix="cursor-mcp-prompt-", suffix=".md")
        os.close(fd)
        Path(prompt_path).write_text(
            prompt.replace("\r\n", "\n").replace("\r", "\n"),
            encoding="utf-8",
            newline="\n",
        )

        cmd = [
            sys.executable,
            str(WRAPPER),
            "--cwd",
            cwd,
            "--mode",
            mode,
            "--model",
            model,
            "--output-format",
            output_format,
            "--timeout",
            str(timeout),
            "--prompt-file",
            prompt_path,
        ]
        if prefer_fast:
            cmd.append("--fast")
        if force:
            cmd.append("--force")
        if skip_preflight:
            cmd.append("--skip-preflight")
        if continue_session:
            cmd.append("--continue-session")
        if require_diff:
            cmd.append("--require-diff")
        if worktree is not None:
            cmd.append("--worktree")
            if worktree:
                cmd.append(worktree)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                timeout=timeout + 30,
                stdin=subprocess.DEVNULL,
                env=_child_env(),
                **_SUBPROCESS_TEXT,
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") + (exc.stderr or "")
            return (
                f"error: timed out after {timeout:g}s — treat as no result; "
                f"retry with a narrower prompt or pass timeout=<higher seconds>\n{out}"
            ).strip()

        parts = []
        if proc.stdout:
            parts.append(proc.stdout.rstrip())
        if proc.stderr:
            parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
        if proc.returncode != 0:
            parts.append(f"[exit {proc.returncode}]")
        return "\n".join(parts) if parts else f"[exit {proc.returncode}] (empty output)"
    finally:
        if prompt_path:
            try:
                os.unlink(prompt_path)
            except OSError:
                pass


@mcp.tool()
def cursor_ask(
    prompt: str,
    cwd: str = ".",
    model: str = "cursor-grok-4.5-high",
    fast: bool = False,
    skip_preflight: bool = True,
    continue_session: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Read-only Cursor ask (--mode ask).

    Default model cursor-grok-4.5-high; caller picks low|medium|high and whether to use Fast
    (fast=true or *-fast id). Pass composer-2.5 for cheaper mechanical Q&A.

    Parent/orchestrator owns timeout (default 1200s, or CURSOR_HEADLESS_TIMEOUT).
    Raise for broad multi-app maps; lower for tiny one-shot Q&A. On timeout: no result —
    narrow the prompt or raise timeout and retry.
    """
    return _run_wrapper(
        prompt=prompt,
        cwd=cwd,
        mode="ask",
        model=model,
        prefer_fast=fast,
        force=False,
        worktree=None,
        skip_preflight=skip_preflight,
        output_format="text",
        continue_session=continue_session,
        timeout=timeout,
        require_diff=False,
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
) -> str:
    """Read-only Cursor plan/explore (--mode plan).

    Default model cursor-grok-4.5-high; caller picks low|medium|high and whether to use Fast
    (fast=true or *-fast id). Pass composer-2.5 for cheaper plans.

    Parent/orchestrator owns timeout (default 1200s, or CURSOR_HEADLESS_TIMEOUT).
    Broad duplicate/consumer inventory often needs 1200–1800 or a narrower path slice.
    On timeout: treat as no result — do not invent findings; narrow or raise timeout.
    """
    return _run_wrapper(
        prompt=prompt,
        cwd=cwd,
        mode="plan",
        model=model,
        prefer_fast=fast,
        force=False,
        worktree=None,
        skip_preflight=skip_preflight,
        output_format="text",
        continue_session=continue_session,
        timeout=timeout,
        require_diff=False,
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
    return _run_wrapper(
        prompt=prompt,
        cwd=cwd,
        mode="default",
        model=model,
        prefer_fast=fast,
        force=force,
        worktree=worktree,
        skip_preflight=skip_preflight,
        output_format="text",
        continue_session=continue_session,
        timeout=timeout,
        require_diff=require_diff,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
