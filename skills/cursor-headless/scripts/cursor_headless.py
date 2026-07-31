#!/usr/bin/env python3
"""Fast Cursor Agent headless wrapper for bounded Codex→Cursor delegation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

ALLOWED_MODELS = {
    "composer-2.5",
    "composer-2.5-fast",
    "cursor-grok-4.5-high",
    "cursor-grok-4.5-high-fast",
    "cursor-grok-4.5-medium",
    "cursor-grok-4.5-medium-fast",
    "cursor-grok-4.5-low",
    "cursor-grok-4.5-low-fast",
}

PREFLIGHT_CACHE = Path(
    os.environ.get(
        "CURSOR_HEADLESS_PREFLIGHT_CACHE",
        str(Path.home() / ".cache" / "cursor-headless" / "preflight.json"),
    )
)
PREFLIGHT_TTL_SEC = float(os.environ.get("CURSOR_HEADLESS_PREFLIGHT_TTL", "3600"))

# Windows .cmd shims mangle newlines in argv; always stage multiline / win32 prompts.
_SUBPROCESS_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Cursor Agent in bounded headless mode.")
    parser.add_argument("prompt", nargs="?", help="Prompt to send to Cursor Agent.")
    parser.add_argument("--prompt-file", help="Read prompt from a file instead of argv.")
    parser.add_argument("--cwd", default=".", help="Workspace directory for Cursor Agent.")
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Cursor model id (default: cursor-grok-4.5-high for ask/plan; "
            "composer-2.5 for default/write mode). Pass --fast or *-fast ids when speed matters."
        ),
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Prefer the *-fast variant of the selected model family when available.",
    )
    parser.add_argument("--mode", default="ask", choices=["ask", "plan", "default"])
    parser.add_argument(
        "--output-format",
        default="text",
        choices=["text", "json", "stream-json"],
        help="Default text (fastest). Use json/stream-json when the orchestrator must parse structure.",
    )
    parser.add_argument(
        "--sandbox",
        default="disabled" if os.name == "nt" else "enabled",
        choices=["enabled", "disabled"],
        help="Cursor sandbox (default: disabled on Windows — sandbox is macOS/Linux only).",
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="Maximum runtime in seconds.")
    parser.add_argument("--add-dir", action="append", default=[], help="Additional workspace root.")
    parser.add_argument("--plugin-dir", action="append", default=[], help="Local Cursor plugin directory.")
    parser.add_argument("--worktree", nargs="?", const="", help="Run in an isolated Cursor worktree.")
    parser.add_argument("--worktree-base", help="Branch or ref for the Cursor worktree base.")
    parser.add_argument("--auto-review", action="store_true", help="Enable Cursor Smart Auto tool review.")
    parser.add_argument("--force", action="store_true", help="Allow commands unless explicitly denied.")
    parser.add_argument("--approve-mcps", action="store_true", help="Automatically approve all MCP servers.")
    parser.add_argument("--no-trust", action="store_true", help="Do not pass --trust.")
    parser.add_argument("--stream-partial-output", action="store_true")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Force a fresh preflight (version/status/models) even if cache is warm.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight entirely (fastest; assume cursor-agent is ready).",
    )
    parser.add_argument(
        "--require-diff",
        action="store_true",
        help="After a write run, fail if git status --porcelain is empty (fake success).",
    )
    parser.add_argument(
        "--inline-prompt",
        action="store_true",
        help="Force prompt as a single argv element (unsafe on Windows; for one-line smoke tests).",
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("--resume", nargs="?", const="", help="Resume a Cursor chat, optionally by chat id.")
    session_group.add_argument("--continue-session", action="store_true", help="Continue the previous Cursor session.")
    parser.add_argument("--raw", action="store_true", help="Print Cursor stdout without JSON summarization.")
    parser.add_argument(
        "--pretty-json",
        action="store_true",
        help="Pretty-print JSON output (slower). Default is compact/raw for speed.",
    )
    return parser.parse_args()


def load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        try:
            return Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"Could not read prompt file {args.prompt_file}: {exc}") from None
    if args.prompt:
        return args.prompt
    raise SystemExit("Provide a prompt argument or --prompt-file.")


def resolve_model(model: str, prefer_fast: bool) -> str:
    if "fable" in model.lower():
        raise SystemExit("Fable models are not allowed in Cursor delegation; use composer-2.5 or cursor-grok-4.5-high.")
    resolved = model
    if prefer_fast and not resolved.endswith("-fast"):
        candidate = f"{resolved}-fast"
        # Only auto-upgrade known families we advertise.
        if candidate in ALLOWED_MODELS or resolved in {
            "composer-2.5",
            "cursor-grok-4.5-high",
            "cursor-grok-4.5-medium",
            "cursor-grok-4.5-low",
        }:
            resolved = candidate
    return resolved


def cursor_agent_bin() -> str:
    """Resolve cursor-agent on PATH (Windows: cursor-agent.cmd)."""
    found = shutil.which("cursor-agent")
    if found:
        return found
    if os.name == "nt":
        local = Path.home() / "AppData" / "Local" / "cursor-agent" / "cursor-agent.cmd"
        if local.is_file():
            return str(local)
    raise SystemExit("cursor-agent executable not found on PATH.")


def run_quiet(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        **_SUBPROCESS_TEXT,
    )


def cached_preflight_ok(model: str) -> bool:
    try:
        data = json.loads(PREFLIGHT_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    age = time.time() - float(data.get("ts", 0))
    if age > PREFLIGHT_TTL_SEC:
        return False
    models = data.get("models") or []
    return bool(data.get("ok")) and (model in models or any(model in m for m in models))


def write_preflight_cache(*, version: str, status_ok: bool, models_text: str) -> None:
    PREFLIGHT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ids = []
    for ln in models_text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # "composer-2.5-fast - Composer 2.5 Fast"
        ids.append(ln.split(" - ", 1)[0].strip())
    payload = {
        "ts": time.time(),
        "ok": status_ok,
        "version": version.strip(),
        "models": sorted(set(ids)),
    }
    PREFLIGHT_CACHE.write_text(json.dumps(payload), encoding="utf-8")


def ensure_preflight(model: str, *, force: bool, skip: bool) -> None:
    if skip:
        return
    if not force and cached_preflight_ok(model):
        return
    agent = cursor_agent_bin()

    version = run_quiet([agent, "--version"])
    status = run_quiet([agent, "status"])
    models = run_quiet([agent, "models"], timeout=120.0)

    status_ok = status.returncode == 0 and "Logged in" in (status.stdout + status.stderr)
    models_text = models.stdout or ""
    if model not in models_text and not any(model in ln for ln in models_text.splitlines()):
        raise SystemExit(
            f"Requested model {model!r} not listed by `cursor-agent models`. "
            "Check account access or pick composer-2.5 / cursor-grok-4.5-high."
        )
    if not status_ok:
        raise SystemExit(
            "cursor-agent is not authenticated. Run `cursor-agent login` or set CURSOR_API_KEY."
        )
    write_preflight_cache(version=version.stdout or "", status_ok=status_ok, models_text=models_text)


def should_stage_task_file(prompt: str, *, force_inline: bool) -> bool:
    if force_inline:
        return False
    if os.name == "nt":
        return True
    if "\n" in prompt or "\r" in prompt:
        return True
    return len(prompt) > 800


def stage_task_file(workspace: Path, prompt: str) -> tuple[str, Path]:
    """Write unique CURSOR_TASK-*.md; return one-line bootstrap argv + path."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    unique = f"{digest}-{os.getpid()}-{int(time.time() * 1000) % 1_000_000}"
    path = workspace / f"CURSOR_TASK-{unique}.md"
    path.write_text(prompt.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="\n")
    bootstrap = (
        f"Read UTF-8 file {path.name} in the workspace root and execute that task fully. "
        f"Do not invent completion. When finished, leave the file; the wrapper deletes it."
    )
    return bootstrap, path


def build_command(args: argparse.Namespace, prompt: str, model: str) -> list[str]:
    agent = cursor_agent_bin()
    if args.force and args.mode != "default":
        raise SystemExit("--force is only allowed with --mode default.")
    if args.stream_partial_output and args.output_format != "stream-json":
        raise SystemExit("--stream-partial-output requires --output-format stream-json.")
    if args.worktree_base and args.worktree is None:
        raise SystemExit("--worktree-base requires --worktree.")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero.")

    workspace = str(Path(args.cwd).resolve())
    cmd = [
        agent,
        "--print",
        "--model",
        model,
        "--output-format",
        args.output_format,
        "--sandbox",
        args.sandbox,
        "--workspace",
        workspace,
    ]

    if args.mode != "default":
        cmd.extend(["--mode", args.mode])
    if not args.no_trust:
        cmd.append("--trust")
    for path in args.add_dir:
        cmd.extend(["--add-dir", path])
    for path in args.plugin_dir:
        cmd.extend(["--plugin-dir", path])
    if args.worktree is not None:
        cmd.append("--worktree")
        if args.worktree:
            cmd.append(args.worktree)
    if args.worktree_base:
        cmd.extend(["--worktree-base", args.worktree_base])
    if args.auto_review:
        cmd.append("--auto-review")
    if args.force:
        cmd.append("--force")
    if args.approve_mcps:
        cmd.append("--approve-mcps")
    if args.stream_partial_output:
        cmd.append("--stream-partial-output")
    if args.resume is not None:
        cmd.append("--resume")
        if args.resume:
            cmd.append(args.resume)
    if args.continue_session:
        cmd.append("--continue")

    cmd.append(prompt)
    return cmd


def summarize_json(stdout: str, *, pretty: bool) -> str:
    data = json.loads(stdout)
    if pretty:
        return json.dumps(data, indent=2, sort_keys=True)
    return json.dumps(data, separators=(",", ":"))


def git_evidence(cwd: str) -> tuple[str, bool]:
    """Return (report block, has_diff). has_diff True if porcelain non-empty."""
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            **_SUBPROCESS_TEXT,
        )
        ds = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=30,
            stdin=subprocess.DEVNULL,
            **_SUBPROCESS_TEXT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"\n--- wrapper git evidence ---\n(unavailable: {exc})\n", False

    porcelain = (st.stdout or "").rstrip()
    diffstat = (ds.stdout or "").rstrip()
    has_diff = bool(porcelain.strip())
    block = (
        "\n--- wrapper git evidence ---\n"
        f"git status --porcelain:\n{porcelain if porcelain else '(clean)'}\n"
        f"git diff --stat HEAD:\n{diffstat if diffstat else '(empty)'}\n"
    )
    return block, has_diff


def run_streaming(cmd: list[str], cwd: str, timeout: float) -> int:
    started_at = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=1,
        **_SUBPROCESS_TEXT,
    )
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)

    while True:
        elapsed = time.monotonic() - started_at
        remaining = timeout - elapsed
        if remaining <= 0:
            proc.kill()
            print(f"cursor-agent timed out after {timeout:g}s", file=sys.stderr)
            return 124

        for key, _ in selector.select(timeout=min(0.25, remaining)):
            line = key.fileobj.readline()
            if line:
                print(line, end="", flush=True)
            else:
                selector.unregister(key.fileobj)

        if proc.poll() is not None:
            for line in proc.stdout:
                print(line, end="", flush=True)
            return proc.returncode


def main() -> int:
    args = parse_args()
    prompt = load_prompt(args)
    default_model = "composer-2.5" if args.mode == "default" else "cursor-grok-4.5-high"
    model = resolve_model(args.model or default_model, prefer_fast=args.fast)
    ensure_preflight(model, force=args.preflight, skip=args.skip_preflight)

    workspace = Path(args.cwd).resolve()
    task_path: Path | None = None
    delivery = prompt
    if should_stage_task_file(prompt, force_inline=args.inline_prompt):
        delivery, task_path = stage_task_file(workspace, prompt)

    cmd = build_command(args, delivery, model)
    exit_code = 0

    try:
        if args.output_format == "stream-json":
            exit_code = run_streaming(cmd, str(workspace), args.timeout)
        else:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workspace),
                    capture_output=True,
                    check=False,
                    timeout=args.timeout,
                    stdin=subprocess.DEVNULL,
                    **_SUBPROCESS_TEXT,
                )
            except subprocess.TimeoutExpired as exc:
                if exc.stdout:
                    print(exc.stdout, end="")
                if exc.stderr:
                    print(exc.stderr, file=sys.stderr, end="")
                print(f"cursor-agent timed out after {args.timeout:g}s", file=sys.stderr)
                exit_code = 124
            else:
                if proc.stderr:
                    print(proc.stderr, file=sys.stderr, end="")

                if args.raw or args.output_format != "json":
                    print(proc.stdout, end="")
                else:
                    try:
                        print(summarize_json(proc.stdout, pretty=args.pretty_json))
                    except json.JSONDecodeError:
                        print(proc.stdout, end="")

                exit_code = proc.returncode
    finally:
        if task_path is not None:
            try:
                task_path.unlink(missing_ok=True)
            except OSError:
                pass

    evidence, has_diff = git_evidence(str(workspace))
    print(evidence, end="")

    if args.require_diff and args.mode == "default" and exit_code == 0 and not has_diff:
        print(
            "error: --require-diff set but git status --porcelain is clean "
            "(Cursor report claimed success with no tree changes)",
            file=sys.stderr,
        )
        return 2

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
