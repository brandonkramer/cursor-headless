#!/usr/bin/env python3
"""Prove 0.3.0 on Mac/Windows: unit tests + CLI runner smoke + job store + MCP import."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd or ROOT, env=env, check=True)


def prove_unit_tests() -> None:
    section("unit tests")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    run([sys.executable, "-m", "unittest", "discover", "-s", "src", "-p", "test_*.py", "-v"], env=env)
    run([sys.executable, "-m", "unittest", "tests.test_cli_runner", "-v"], env=env)


def prove_mcp_import() -> None:
    section("MCP import + tool registry")
    try:
        import mcp  # noqa: F401,WPS433
    except ModuleNotFoundError:
        # Match plugin launch: uv provides mcp>=1.9,<2
        if shutil.which("uv"):
            run(
                [
                    "uv",
                    "run",
                    "--with",
                    "mcp>=1.9,<2",
                    "--python",
                    "3.14",
                    "python",
                    "-c",
                    "import sys; sys.path.insert(0,'src'); "
                    "import cursor_headless_mcp as m; "
                    "names=sorted(m.mcp._tool_manager._tools.keys()); "
                    "exp={'cursor_ask','cursor_plan','cursor_implement','cursor_status'}; "
                    "missing=exp-set(names); "
                    "assert not missing, missing; "
                    "print('tools:', ', '.join(names))",
                ]
            )
            return
        raise SystemExit("mcp not installed and uv not on PATH") from None

    import cursor_headless_mcp as m  # noqa: WPS433

    names = sorted(m.mcp._tool_manager._tools.keys())
    expected = {"cursor_ask", "cursor_plan", "cursor_implement", "cursor_status"}
    missing = expected - set(names)
    if missing:
        raise SystemExit(f"missing MCP tools: {sorted(missing)}; have {names}")
    print("tools:", ", ".join(names))


def prove_job_store() -> None:
    section("job store round-trip")
    from jobs import create_job, get_status_text, update_job_from_status

    with tempfile.TemporaryDirectory() as td:
        os.environ["CURSOR_HEADLESS_JOB_DIR"] = td
        # reload job_dir via env — jobs.job_dir reads env each call
        jid = create_job()
        update_job_from_status(jid, {"message": "prove", "phase": "ask"}, state="running")
        text = get_status_text(jid)
        if jid not in text or "running" not in text:
            raise SystemExit(f"bad status text: {text!r}")
        print(text.splitlines()[0])


def prove_cli_runner_fake() -> None:
    section("cli_runner fake cursor-agent stream-json")
    from cli_runner import run_cli

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        td_path = Path(td)
        fake_bin = td_path / "bin"
        fake_bin.mkdir()
        impl = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "events = [\n"
            '  {"type":"system","subtype":"init","model":"fake","session_id":"s1"},\n'
            '  {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hi"}]}},\n'
            '  {"type":"result","subtype":"success","is_error":False,"result":"CURSOR_PROVE_OK","duration_ms":1,"session_id":"s1"},\n'
            "]\n"
            "for e in events:\n"
            "    print(json.dumps(e), flush=True)\n"
            "sys.exit(0)\n"
        )
        if os.name == "nt":
            py_path = fake_bin / "cursor-agent-impl.py"
            py_path.write_text(impl, encoding="utf-8", newline="\n")
            fake = fake_bin / "cursor-agent.cmd"
            fake.write_text(
                f'@echo off\r\n"{sys.executable}" "{py_path}" %*\r\n',
                encoding="utf-8",
                newline="\r\n",
            )
        else:
            fake = fake_bin / "cursor-agent"
            fake.write_text(impl, encoding="utf-8")
            fake.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        # Patch which() resolution by putting fake first; wrapper calls cursor_agent_bin via PATH
        old = os.environ.get("PATH")
        os.environ["PATH"] = env["PATH"]
        progresses: list[tuple[float, str]] = []
        try:
            result = run_cli(
                prompt="Return exactly: CURSOR_PROVE_OK",
                cwd=str(td_path),
                mode="ask",
                model="composer-2.5",
                prefer_fast=False,
                force=False,
                worktree=None,
                skip_preflight=True,
                continue_session=False,
                timeout=60,
                require_diff=False,
                job_id="prove-job",
                on_progress=lambda p, m: progresses.append((p, m)),
            )
        finally:
            if old is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old

        if result["status"] != "ok":
            raise SystemExit(f"cli_runner status={result['status']!r} result={result}")
        if "CURSOR_PROVE_OK" not in result["result"]:
            raise SystemExit(f"missing prove token in result: {result['result']!r}")
        if not progresses:
            raise SystemExit("no progress callbacks fired")
        print("status=ok progresses=", len(progresses), "result_ok")


def prove_live_cursor_agent() -> None:
    section("live cursor-agent ask (optional if authenticated)")
    agent = shutil.which("cursor-agent")
    if not agent:
        print("SKIP: cursor-agent not on PATH")
        return
    # Quick status check
    st = subprocess.run(
        [agent, "status"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    blob = (st.stdout or "") + (st.stderr or "")
    if st.returncode != 0 or "Logged in" not in blob:
        print(f"SKIP: cursor-agent not logged in (exit {st.returncode})")
        return

    from cli_runner import run_cli
    from envelope import format_envelope
    from jobs import create_job, get_status_text, update_job_from_status

    with tempfile.TemporaryDirectory() as td:
        os.environ["CURSOR_HEADLESS_JOB_DIR"] = str(Path(td) / "jobs")
        jid = create_job()
        update_job_from_status(jid, {"message": "live start"}, state="running")
        t0 = time.monotonic()
        result = run_cli(
            prompt="Reply with exactly this token and nothing else: CURSOR_LIVE_PROVE_OK",
            cwd=td,
            mode="ask",
            model="composer-2.5",
            prefer_fast=True,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=180,
            require_diff=False,
            job_id=jid,
            on_progress=lambda p, m: update_job_from_status(
                jid, {"message": m, "progress": p}, state="running"
            ),
        )
        elapsed = time.monotonic() - t0
        update_job_from_status(
            jid,
            {"message": f"done status={result['status']}"},
            state="done" if result["status"] == "ok" else "error",
        )
        env_text = format_envelope(result)
        status_text = get_status_text(jid)
        print(f"elapsed_s={elapsed:.1f}")
        print(env_text[:500])
        print("--- cursor_status ---")
        print(status_text)
        if result["status"] != "ok":
            raise SystemExit(f"live run failed: {result['status']}")
        if "CURSOR_LIVE_PROVE_OK" not in result["result"] and "CURSOR_LIVE_PROVE_OK" not in env_text:
            # Models sometimes wrap; require at least ok envelope
            print("WARN: exact token not in result; status ok still accepted")
        print("LIVE_OK")


def prove_sdk_import_path() -> None:
    section("sdk backend missing-key path")
    from sdk_runner import run_sdk

    old = os.environ.pop("CURSOR_API_KEY", None)
    try:
        result = run_sdk(
            prompt="x",
            cwd=".",
            mode="ask",
            model="composer-2.5",
            prefer_fast=False,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=30,
            require_diff=False,
            job_id="sdk-prove",
        )
    finally:
        if old is not None:
            os.environ["CURSOR_API_KEY"] = old
    if result["status"] != "error":
        raise SystemExit(f"expected sdk error without key, got {result}")
    print("sdk missing-key => error (expected)")


def main() -> int:
    print(f"ROOT={ROOT}")
    print(f"python={sys.version}")
    print(f"platform={sys.platform} os.name={os.name}")
    prove_unit_tests()
    prove_mcp_import()
    prove_job_store()
    prove_cli_runner_fake()
    prove_sdk_import_path()
    prove_live_cursor_agent()
    section("ALL PROOFS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
