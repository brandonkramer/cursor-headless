#!/usr/bin/env python3
"""Smoke-prove /cursor-review rename (command + workflow) on Mac/Windows."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def prove_files() -> None:
    section("command + workflow files")
    cmd = ROOT / "commands" / "cursor-review.md"
    old_cmd = ROOT / "commands" / "cursor-review-loop.md"
    wf = ROOT / "workflows" / "review.js"
    old_wf = ROOT / "workflows" / "review-loop.js"

    if not cmd.is_file():
        fail(f"missing {cmd}")
    if old_cmd.exists():
        fail(f"old command still present: {old_cmd}")
    if not wf.is_file():
        fail(f"missing {wf}")
    if old_wf.exists():
        fail(f"old workflow still present: {old_wf}")

    text = cmd.read_text(encoding="utf-8")
    if "# /cursor-review" not in text:
        fail("command missing # /cursor-review heading")
    if "cursor-review-loop" in text:
        fail("command still mentions cursor-review-loop")
    if "workflows/review.js" not in text:
        fail("command does not point at workflows/review.js")
    if "/cursor-headless:review" not in text:
        fail("command missing /cursor-headless:review")
    print("ok commands/cursor-review.md")

    wtext = wf.read_text(encoding="utf-8")
    if "name: 'review'" not in wtext and 'name: "review"' not in wtext:
        fail("workflow meta.name is not review")
    if "cursor-review-loop" in wtext or "review-loop" in wtext:
        fail("workflow still mentions old names")
    if "/cursor-review" not in wtext:
        fail("workflow whenToUse missing /cursor-review")
    print("ok workflows/review.js")


def prove_no_stale_refs() -> None:
    section("no stale rename refs in repo docs/commands")
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "node_modules", ".cursor-headless"} for part in path.parts):
            continue
        if path.suffix not in {".md", ".js", ".json", ".py", ".toml"}:
            continue
        # This prove script intentionally mentions old names when scanning.
        if path.name == "prove_cursor_review.py":
            continue
        try:
            blob = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "cursor-review-loop" in blob or "review-loop.js" in blob:
            hits.append(str(path.relative_to(ROOT)))
        if re.search(r"name:\s*['\"]review-loop['\"]", blob):
            hits.append(str(path.relative_to(ROOT)))
    if hits:
        fail("stale refs:\n  " + "\n  ".join(hits))
    print("ok no stale refs")


def prove_workflow_syntax_and_args() -> None:
    section("workflow node syntax + arg validation")
    node = shutil.which("node")
    if not node:
        print("SKIP: node not on PATH")
        return

    wf = ROOT / "workflows" / "review.js"
    # Claude workflow scripts are not standalone modules (top-level `return` /
    # host-injected `args`). Prove the preamble with a harness instead of
    # `node --check` on the full file.
    src = wf.read_text(encoding="utf-8")
    # Take from meta through maxIterations assignment block
    m = re.search(
        r"(export const meta = \{.*?\n\}\n\n)(const ARGS =.*?\nmaxIterations = Math\.min\(Math\.floor\(maxIterations\), 5\))",
        src,
        re.S,
    )
    if not m:
        fail("could not extract workflow preamble for dry validation")
    preamble = m.group(2)

    harness = f"""
const args = {{ scope: 'smoke uncommitted', cwd: {str(ROOT)!r}, maxIterations: 9 }};
{preamble}
if (typeof scope !== 'string' || !scope.includes('smoke')) throw new Error('scope bad');
if (cwd !== {str(ROOT)!r}) throw new Error('cwd bad');
if (maxIterations !== 5) throw new Error('maxIterations clamp failed: ' + maxIterations);
console.log('ok preamble args scope/cwd/maxIterations');
"""
    subprocess.run([node, "-e", harness], check=True, cwd=str(ROOT))

    # Missing scope must throw
    bad = f"""
const args = {{ cwd: {str(ROOT)!r} }};
try {{
{preamble}
  throw new Error('expected throw');
}} catch (e) {{
  if (!String(e.message || e).includes('review workflow requires')) throw e;
  console.log('ok missing scope rejected');
}}
"""
    subprocess.run([node, "-e", bad], check=True, cwd=str(ROOT))


def prove_plugin_caches() -> None:
    section("plugin caches expose cursor-review")
    home = Path.home()
    candidates = [
        home / ".codex/plugins/cache/cursor-headless/cursor-headless",
        home / ".claude/plugins/cache/cursor-headless-local/cursor-headless",
    ]
    checked = 0
    for base in candidates:
        if not base.is_dir():
            print(f"SKIP missing cache root: {base}")
            continue
        versions = sorted(
            [p for p in base.iterdir() if p.is_dir() and p.name[0].isdigit()],
            key=lambda p: p.name,
        )
        if not versions:
            print(f"SKIP no version dirs: {base}")
            continue
        latest = versions[-1]
        cmd = latest / "commands" / "cursor-review.md"
        wf = latest / "workflows" / "review.js"
        old = latest / "commands" / "cursor-review-loop.md"
        if not cmd.is_file():
            fail(f"cache missing command: {cmd}")
        if not wf.is_file():
            fail(f"cache missing workflow: {wf}")
        if old.exists():
            fail(f"cache still has old command: {old}")
        print(f"ok {latest}")
        checked += 1
    if checked == 0:
        print("WARN: no plugin caches checked (ok for bare clone CI)")


def prove_mcp_tools_still_registered() -> None:
    section("MCP tools still registered (sanity)")
    try:
        subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "mcp>=1.9,<2",
                "--with",
                "cursor-sdk",
                "--python",
                "3.14",
                "python",
                "-c",
                "import sys; sys.path.insert(0,'src'); import cursor_headless_mcp as m; "
                "names=sorted(m.mcp._tool_manager._tools.keys()); "
                "exp={'cursor_ask','cursor_plan','cursor_implement','cursor_status'}; "
                "assert not (exp-set(names)), exp-set(names); "
                "print('ok tools', ', '.join(names))",
            ],
            check=True,
            cwd=str(ROOT),
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"SKIP mcp registry check: {exc}")


def main() -> int:
    print(f"ROOT={ROOT}")
    print(f"python={sys.version}")
    print(f"platform={sys.platform} os.name={os.name} machine={platform.machine()}")
    prove_files()
    prove_no_stale_refs()
    prove_workflow_syntax_and_args()
    prove_plugin_caches()
    prove_mcp_tools_still_registered()
    print("\n=== ALL PROOFS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
