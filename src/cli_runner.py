"""Run cursor_headless.py with stream-json and parse NDJSON progress."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
from typing import TypedDict

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PLUGIN_ROOT / "skills" / "cursor-headless" / "scripts" / "cursor_headless.py"

ProgressCallback = Callable[[float, str], None]
StatusCallback = Callable[[dict[str, object]], None]


class CliRunResult(TypedDict):
    status: str
    backend: str
    job_id: str
    model: str
    elapsed_s: float
    tools: int | None
    progress_summary: str
    result: str
    stderr: str
    exit_code: int


@dataclass
class _StreamState:
    tools: int = 0
    summary_lines: list[str] = field(default_factory=list)
    result_text: str = ""
    model: str = ""
    progress: float = 0.0
    non_json_lines: list[str] = field(default_factory=list)


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _kill_process_tree(proc: Popen[str]) -> None:
    """Kill wrapper + children (Windows needs taskkill /T)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _extract_assistant_snippet(event: dict[str, object]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    joined = " ".join(parts)
    if len(joined) > 120:
        return joined[:117] + "..."
    return joined


def _tool_started_message(event: dict[str, object]) -> str:
    tool_call = event.get("tool_call")
    if not isinstance(tool_call, dict):
        return "tool started"
    for key, label in (
        ("writeToolCall", "Write"),
        ("readToolCall", "Read"),
        ("shellToolCall", "Shell"),
        ("grepToolCall", "Grep"),
    ):
        detail = tool_call.get(key)
        if isinstance(detail, dict):
            args = detail.get("args")
            path = ""
            if isinstance(args, dict):
                raw_path = args.get("path") or args.get("command") or args.get("pattern")
                if isinstance(raw_path, str):
                    path = raw_path
            if path:
                return f"{label}: {path}"
            return label
    fn = tool_call.get("function")
    if isinstance(fn, dict):
        name = fn.get("name")
        if isinstance(name, str) and name:
            return f"tool: {name}"
    return "tool started"


def _handle_event(
    state: _StreamState,
    event: dict[str, object],
    *,
    on_progress: ProgressCallback | None,
    on_status: StatusCallback | None,
) -> None:
    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        raw_model = event.get("model")
        if isinstance(raw_model, str) and raw_model:
            state.model = raw_model
        state.progress = max(state.progress, 0.05)
        msg = f"init model={state.model or 'unknown'}"
        state.summary_lines.append(msg)
        if on_progress:
            on_progress(state.progress, msg)
        if on_status:
            on_status(event)
        return

    if etype == "assistant":
        snippet = _extract_assistant_snippet(event)
        if snippet:
            state.progress = min(0.92, max(state.progress, 0.08))
            msg = f"assistant: {snippet}"
            state.summary_lines.append(msg)
            if on_progress:
                on_progress(state.progress, msg)
        if on_status:
            on_status(event)
        return

    if etype == "tool_call" and event.get("subtype") == "started":
        state.tools += 1
        state.progress = min(0.95, 0.1 + state.tools * 0.08)
        msg = f"tool #{state.tools} {_tool_started_message(event)}"
        state.summary_lines.append(msg)
        if on_progress:
            on_progress(state.progress, msg)
        if on_status:
            on_status(event)
        return

    if etype == "result":
        raw_result = event.get("result")
        if isinstance(raw_result, str):
            state.result_text = raw_result
        state.progress = 1.0
        subtype = event.get("subtype")
        msg = f"result ({subtype or 'unknown'})"
        state.summary_lines.append(msg)
        if on_progress:
            on_progress(state.progress, msg)
        if on_status:
            on_status(event)
        return

    if on_status:
        on_status(event)


def parse_ndjson_line(line: str, state: _StreamState) -> None:
    """Parse one stdout line; append non-JSON lines to state.non_json_lines."""
    stripped = line.strip()
    if not stripped:
        return
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        state.non_json_lines.append(line.rstrip("\n"))
        return
    if isinstance(parsed, dict):
        _handle_event(state, parsed, on_progress=None, on_status=None)


def _build_wrapper_cmd(
    *,
    prompt_path: str,
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
) -> list[str]:
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
        "stream-json",
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
    return cmd


def _resolve_status(*, timed_out: bool, exit_code: int, result_text: str) -> str:
    if timed_out or exit_code == 124:
        return "timeout"
    if exit_code != 0:
        return "error"
    if not result_text.strip():
        return "error"
    return "ok"


def run_cli(
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
    """Run the wrapper with stream-json; return a structured result dict."""
    resolved_job_id = job_id or uuid.uuid4().hex[:12]
    empty: CliRunResult = {
        "status": "error",
        "backend": "cli",
        "job_id": resolved_job_id,
        "model": model,
        "elapsed_s": 0.0,
        "tools": None,
        "progress_summary": "",
        "result": "",
        "stderr": "",
        "exit_code": -1,
    }

    if not WRAPPER.is_file():
        empty["result"] = f"error: wrapper missing at {WRAPPER}"
        return empty

    prompt_path: str | None = None
    started = time.monotonic()
    outer_timeout = timeout + 30.0

    try:
        fd, prompt_path = tempfile.mkstemp(prefix="cursor-cli-prompt-", suffix=".md")
        os.close(fd)
        Path(prompt_path).write_text(
            prompt.replace("\r\n", "\n").replace("\r", "\n"),
            encoding="utf-8",
            newline="\n",
        )

        cmd = _build_wrapper_cmd(
            prompt_path=prompt_path,
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
        )

        proc = Popen(
            cmd,
            stdout=PIPE,
            stderr=PIPE,
            stdin=DEVNULL,
            env=_child_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            if proc.stderr is None:
                return
            for err_line in proc.stderr:
                stderr_lines.append(err_line.rstrip("\n"))

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        state = _StreamState()
        timed_out = False

        assert proc.stdout is not None
        while True:
            elapsed = time.monotonic() - started
            if elapsed > outer_timeout:
                timed_out = True
                _kill_process_tree(proc)
                break

            line = proc.stdout.readline()
            if line:
                stripped = line.strip()
                if stripped:
                    try:
                        parsed = json.loads(stripped)
                        if isinstance(parsed, dict):
                            _handle_event(
                                state,
                                parsed,
                                on_progress=on_progress,
                                on_status=on_status,
                            )
                        else:
                            state.non_json_lines.append(line.rstrip("\n"))
                    except json.JSONDecodeError:
                        state.non_json_lines.append(line.rstrip("\n"))
                continue

            if proc.poll() is not None:
                for tail in proc.stdout:
                    tail_stripped = tail.strip()
                    if not tail_stripped:
                        continue
                    try:
                        parsed = json.loads(tail_stripped)
                        if isinstance(parsed, dict):
                            _handle_event(
                                state,
                                parsed,
                                on_progress=on_progress,
                                on_status=on_status,
                            )
                        else:
                            state.non_json_lines.append(tail.rstrip("\n"))
                    except json.JSONDecodeError:
                        state.non_json_lines.append(tail.rstrip("\n"))
                break

            if time.monotonic() - started > timeout:
                timed_out = True
                _kill_process_tree(proc)
                break

            time.sleep(0.05)

        exit_code = proc.wait(timeout=5)
        stderr_thread.join(timeout=2.0)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        elapsed_s = time.monotonic() - started

        stderr = "\n".join(stderr_lines).strip()
        result_text = state.result_text
        if state.non_json_lines:
            tail = "\n".join(state.non_json_lines).strip()
            if tail:
                result_text = f"{result_text.rstrip()}\n{tail}".strip() if result_text else tail

        status = _resolve_status(timed_out=timed_out, exit_code=exit_code, result_text=result_text)
        if status == "timeout":
            timeout_msg = (
                f"error: timed out after {timeout:g}s — treat as no result; "
                f"retry with a narrower prompt or pass timeout=<higher seconds>"
            )
            result_text = f"{timeout_msg}\n{result_text}".strip() if result_text else timeout_msg

        return {
            "status": status,
            "backend": "cli",
            "job_id": resolved_job_id,
            "model": state.model or model,
            "elapsed_s": round(elapsed_s, 2),
            "tools": state.tools if state.tools > 0 else None,
            "progress_summary": "\n".join(state.summary_lines),
            "result": result_text,
            "stderr": stderr,
            "exit_code": exit_code,
        }
    finally:
        if prompt_path:
            try:
                os.unlink(prompt_path)
            except OSError:
                pass
