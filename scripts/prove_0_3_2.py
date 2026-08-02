#!/usr/bin/env python3
"""Prove 0.3.2: SDK Fast via ModelSelection params (Mac/Windows)."""

from __future__ import annotations

import os
import platform
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


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)


def prove_unit_tests() -> None:
    section("unit tests")
    py = sys.executable
    run([py, "-m", "unittest", "discover", "-s", "src", "-p", "test_*.py", "-v"])
    run([py, "-m", "unittest", "tests.test_cli_runner", "-v"])


def prove_parse_mapping() -> None:
    section("sdk model parse mapping")
    from sdk_runner import _build_model_selection, _parse_sdk_model

    cases = [
        ("composer-2.5", True, "composer-2.5", True, None),
        ("composer-2.5-fast", False, "composer-2.5", True, None),
        ("composer-2.5", False, "composer-2.5", False, None),
        ("cursor-grok-4.5-high", True, "grok-4.5", True, "high"),
        ("cursor-grok-4.5-medium-fast", False, "grok-4.5", True, "medium"),
    ]
    for raw, prefer, mid, fast, effort in cases:
        spec = _parse_sdk_model(raw, prefer)
        assert spec.model_id == mid, (raw, spec)
        assert spec.fast is fast, (raw, spec)
        assert spec.effort == effort, (raw, spec)
        assert "-fast" not in spec.model_id, spec
        print(f"ok {raw!r} prefer_fast={prefer} -> {spec.label}")

    # Dict fallback when ModelSelection missing
    class _EmptySdk:
        pass

    sel = _build_model_selection(_EmptySdk(), _parse_sdk_model("composer-2.5", True))
    assert isinstance(sel, dict)
    assert sel["id"] == "composer-2.5"
    assert sel["params"] == [{"id": "fast", "value": "true"}]
    print("ok dict fallback ModelSelection")


def prove_live_sdk_fast() -> None:
    section("live SDK ask with prefer_fast (needs CURSOR_API_KEY)")
    key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not key.startswith("crsr_"):
        print("SKIP: CURSOR_API_KEY not set")
        return

    from runner import run_cursor

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        Path(td, "README.md").write_text("prove\n", encoding="utf-8")
        t0 = time.perf_counter()
        result = run_cursor(
            prompt="Reply with exactly TOKEN_SDK_FAST_OK and nothing else.",
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
            backend="sdk",
        )
        dt = time.perf_counter() - t0
        status = result.get("status")
        model = result.get("model")
        text = (result.get("result") or "").replace("\n", " ")
        if "crsr_" in text:
            text = "[REDACTED]"
        print(f"wall={dt:.2f}s status={status} model={model!r}")
        print(f"result={text[:160]!r}")
        if status != "ok":
            raise SystemExit(f"live SDK prefer_fast failed: {text[:300]}")
        if "TOKEN_SDK_FAST_OK" not in text:
            raise SystemExit(f"missing prove token: {text[:300]}")
        # Must not have requested invalid *-fast model id
        err = (result.get("stderr") or "") + text
        if "composer-2.5-fast" in err and "Cannot use this model" in err:
            raise SystemExit("regress: still sending composer-2.5-fast id")
        print("LIVE_SDK_FAST_OK")


def main() -> int:
    print(f"ROOT={ROOT}")
    print(f"python={sys.version}")
    print(f"platform={sys.platform} os.name={os.name} machine={platform.machine()}")
    prove_unit_tests()
    prove_parse_mapping()
    prove_live_sdk_fast()
    print("\n=== ALL PROOFS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
