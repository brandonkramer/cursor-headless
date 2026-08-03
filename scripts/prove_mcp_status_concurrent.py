#!/usr/bin/env python3
"""Live stdio MCP proof: cursor_status answers while a long cursor_ask is in flight.

Spawns a shim MCP server that blocks inside run_cursor (no Cursor API needed),
issues cursor_ask, then polls cursor_status with a hard timeout. Fail if status
blocks behind the long tool (the pre-0.3.10 hang).
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
STATUS_TIMEOUT_SEC = 2.0
BLOCK_SEC = 4.0


async def _wait_for_file(path: Path, timeout_sec: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if path.is_file():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {path}")


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cursor-headless-mcp-proof-") as tmp:
        tmp_path = Path(tmp)
        job_dir = tmp_path / "jobs"
        started = tmp_path / "started"
        job_dir.mkdir()

        env = os.environ.copy()
        env["CURSOR_HEADLESS_JOB_DIR"] = str(job_dir)
        env["CURSOR_HEADLESS_FAKE_BLOCK_SEC"] = str(BLOCK_SEC)
        env["CURSOR_HEADLESS_FAKE_STARTED_PATH"] = str(started)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        server = StdioServerParameters(
            command="uv",
            args=[
                "run",
                "--with",
                "mcp>=1.9,<2",
                "--python",
                "3.14",
                "python",
                str(ROOT / "scripts" / "mcp_fake_block_server.py"),
            ],
            cwd=str(ROOT),
            env=env,
        )

        print("=== prove_mcp_status_concurrent ===")
        print(f"block_sec={BLOCK_SEC} status_timeout={STATUS_TIMEOUT_SEC}")

        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert "cursor_ask" in tools and "cursor_status" in tools, tools

                ask_task = asyncio.create_task(
                    session.call_tool(
                        "cursor_ask",
                        {"prompt": "fake", "cwd": str(ROOT), "timeout": 30},
                    )
                )

                await _wait_for_file(started)
                print("worker entered fake block")

                t0 = time.monotonic()
                try:
                    status_result = await asyncio.wait_for(
                        session.call_tool("cursor_status", {}),
                        timeout=STATUS_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    print(
                        f"FAIL: cursor_status did not return within {STATUS_TIMEOUT_SEC}s "
                        "(still blocked behind cursor_ask)"
                    )
                    ask_task.cancel()
                    return 1
                status_elapsed = time.monotonic() - t0

                status_text = ""
                for block in status_result.content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        status_text += text
                print(f"status_elapsed_sec={status_elapsed:.3f}")
                print(f"status_text={status_text!r}")

                if status_elapsed >= STATUS_TIMEOUT_SEC:
                    print("FAIL: status too slow")
                    ask_task.cancel()
                    return 1
                if "fake-block-in-worker" not in status_text and "running" not in status_text:
                    print("FAIL: status did not show in-flight job")
                    ask_task.cancel()
                    return 1

                ask_result = await asyncio.wait_for(ask_task, timeout=BLOCK_SEC + 5.0)
                ask_text = ""
                for block in ask_result.content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        ask_text += text
                print(f"ask_finished status_ok={'status: ok' in ask_text}")
                if "status: ok" not in ask_text:
                    print(f"FAIL: ask envelope unexpected: {ask_text[:300]!r}")
                    return 1

        print("PASS: cursor_status answered during in-flight cursor_ask over stdio MCP")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
