#!/usr/bin/env python3
"""Prove 0.3.4: auto backend + live CLI/SDK on Mac and Windows."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def prove_unit_tests() -> None:
    section("unit tests")
    py = sys.executable
    run([py, "-m", "unittest", "discover", "-s", "src", "-p", "test_*.py", "-v"])
    run([py, "-m", "unittest", "tests.test_cli_runner", "-v"])


def prove_resolve_matrix() -> None:
    section("resolve_backend matrix")
    from runner import resolve_backend

    prev_b = os.environ.pop("CURSOR_HEADLESS_BACKEND", None)
    prev_k = os.environ.pop("CURSOR_API_KEY", None)
    try:
        assert resolve_backend(None) == "cli"
        print("ok no-key -> cli")

        os.environ["CURSOR_API_KEY"] = "crsr_dummy"
        assert resolve_backend(None) == "sdk"
        print("ok key -> sdk")

        assert resolve_backend("cli") == "cli"
        print("ok explicit cli with key")

        os.environ["CURSOR_HEADLESS_BACKEND"] = "cli"
        assert resolve_backend(None) == "cli"
        print("ok env cli overrides key auto")
        os.environ.pop("CURSOR_HEADLESS_BACKEND", None)

        assert resolve_backend("sdk") == "sdk"
        print("ok explicit sdk")
    finally:
        if prev_b is None:
            os.environ.pop("CURSOR_HEADLESS_BACKEND", None)
        else:
            os.environ["CURSOR_HEADLESS_BACKEND"] = prev_b
        if prev_k is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = prev_k


def _ask(
    *,
    prompt: str,
    token: str,
    backend: str | None,
    prefer_fast: bool = True,
) -> dict[str, object]:
    from runner import run_cursor

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        Path(td, "README.md").write_text("prove\n", encoding="utf-8")
        t0 = time.perf_counter()
        result = run_cursor(
            prompt=prompt,
            cwd=td,
            mode="ask",
            model="composer-2.5",
            prefer_fast=prefer_fast,
            force=False,
            worktree=None,
            skip_preflight=True,
            continue_session=False,
            timeout=180,
            require_diff=False,
            backend=backend,
        )
        dt = time.perf_counter() - t0
        text = (result.get("result") or "").replace("\n", " ")
        if "crsr_" in text:
            text = "[REDACTED]"
        print(
            f"backend_arg={backend!r} got={result.get('backend')!r} "
            f"status={result.get('status')} wall={dt:.2f}s model={result.get('model')!r}",
            flush=True,
        )
        print(f"  result={text[:140]!r}", flush=True)
        if result.get("status") != "ok":
            raise SystemExit(f"ask failed: {text[:300]}")
        if token not in text:
            raise SystemExit(f"missing token {token}: {text[:300]}")
        return result


def prove_live_cli() -> None:
    section("live CLI ask (cursor-agent)")
    agent = shutil.which("cursor-agent")
    if not agent:
        print("SKIP: cursor-agent not on PATH")
        return
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

    # Force CLI even if API key present in env
    result = _ask(
        prompt="Reply with exactly TOKEN_CLI_PROVE_OK and nothing else.",
        token="TOKEN_CLI_PROVE_OK",
        backend="cli",
    )
    assert result.get("backend") == "cli", result
    print("LIVE_CLI_OK")


def prove_live_sdk_auto() -> None:
    section("live SDK auto (CURSOR_API_KEY present, backend=None)")
    key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not key.startswith("crsr_"):
        raise SystemExit(
            "FAIL: CURSOR_API_KEY required for full prove "
            "(set in env / pass-cli inject before running this script)"
        )

    from runner import resolve_backend

    assert resolve_backend(None) == "sdk", resolve_backend(None)
    print("ok resolve_backend(None) == sdk with key")

    result = _ask(
        prompt="Reply with exactly TOKEN_SDK_AUTO_OK and nothing else.",
        token="TOKEN_SDK_AUTO_OK",
        backend=None,  # auto
        prefer_fast=True,
    )
    assert result.get("backend") == "sdk", result
    print("LIVE_SDK_AUTO_OK")

    # Force CLI while key still set
    result = _ask(
        prompt="Reply with exactly TOKEN_FORCE_CLI_OK and nothing else.",
        token="TOKEN_FORCE_CLI_OK",
        backend="cli",
    )
    assert result.get("backend") == "cli", result
    print("LIVE_FORCE_CLI_WITH_KEY_OK")


def main() -> int:
    print(f"ROOT={ROOT}")
    print(f"python={sys.version}")
    print(f"platform={sys.platform} os.name={os.name} machine={platform.machine()}")
    print(f"has_CURSOR_API_KEY={bool(os.environ.get('CURSOR_API_KEY','').strip())}")
    prove_unit_tests()
    prove_resolve_matrix()
    prove_live_cli()
    prove_live_sdk_auto()
    print("\n=== ALL PROOFS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
