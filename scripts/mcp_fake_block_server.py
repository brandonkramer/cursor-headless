#!/usr/bin/env python3
"""MCP server shim: block inside run_cursor so concurrency proofs need no live Cursor agent."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import cursor_headless_mcp as mcp_mod  # noqa: E402
import jobs  # noqa: E402


def fake_run_cursor(**kwargs: object) -> dict[str, object]:
    job_id = str(kwargs["job_id"])
    block_sec = float(os.environ.get("CURSOR_HEADLESS_FAKE_BLOCK_SEC", "3"))
    jobs.update_job_from_status(
        job_id,
        {
            "phase": "ask",
            "model": "fake",
            "message": "fake-block-in-worker",
            "progress": 0.25,
        },
        state="running",
    )
    # Marker for the client: worker entered the block.
    marker = os.environ.get("CURSOR_HEADLESS_FAKE_STARTED_PATH")
    if marker:
        Path(marker).write_text("1", encoding="utf-8")
    time.sleep(block_sec)
    return {
        "status": "ok",
        "backend": "cli",
        "job_id": job_id,
        "model": "fake",
        "elapsed_s": block_sec,
        "tools": 0,
        "result": "fake-done",
        "progress_summary": "fake block complete",
    }


def main() -> None:
    with mock.patch.object(mcp_mod, "run_cursor", side_effect=fake_run_cursor):
        mcp_mod.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
