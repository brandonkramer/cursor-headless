"""Prove long Cursor work can run off-thread while cursor_status stays responsive."""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import time
import unittest
from unittest import mock

import cursor_headless_mcp as mcp_mod
import jobs


class McpConcurrencyTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._previous_job_dir = os.environ.get("CURSOR_HEADLESS_JOB_DIR")
        os.environ["CURSOR_HEADLESS_JOB_DIR"] = self._tmpdir.name
        self._started_path = os.path.join(self._tmpdir.name, "started")
        self._release_path = os.path.join(self._tmpdir.name, "release")

    async def asyncTearDown(self) -> None:
        if self._previous_job_dir is None:
            os.environ.pop("CURSOR_HEADLESS_JOB_DIR", None)
        else:
            os.environ["CURSOR_HEADLESS_JOB_DIR"] = self._previous_job_dir
        self._tmpdir.cleanup()

    async def _wait_for_file(self, path: str, timeout_sec: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if os.path.isfile(path):
                return
            await asyncio.sleep(0.02)
        raise TimeoutError(f"timed out waiting for {path}")

    async def test_status_responsive_while_dispatch_offloaded(self) -> None:
        """asyncio.to_thread yields the event loop so status reads are not blocked."""
        job_id_box: dict[str, str] = {}

        def fake_run_cursor(**kwargs: object) -> dict[str, object]:
            jid = str(kwargs["job_id"])
            job_id_box["id"] = jid
            jobs.update_job_from_status(
                jid,
                {"phase": "ask", "message": "blocked-in-worker", "progress": 0.2},
                state="running",
            )
            with open(self._started_path, "w", encoding="utf-8") as handle:
                handle.write("1")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if os.path.isfile(self._release_path):
                    break
                time.sleep(0.02)
            else:
                raise TimeoutError("worker never saw release")
            return {
                "status": "ok",
                "backend": "cli",
                "model": "cursor-grok-4.5-high",
                "tools": 0,
                "elapsed_s": 0.1,
                "job_id": jid,
                "result": "done",
                "progress_summary": "ok",
            }

        with mock.patch.object(mcp_mod, "run_cursor", side_effect=fake_run_cursor):
            task = asyncio.create_task(
                mcp_mod._run_dispatch(
                    prompt="hi",
                    cwd=".",
                    mode="ask",
                    model="cursor-grok-4.5-high",
                    prefer_fast=False,
                    force=False,
                    worktree=None,
                    skip_preflight=True,
                    continue_session=False,
                    timeout=30.0,
                    require_diff=False,
                    backend="cli",
                    ctx=None,
                    progress=True,
                )
            )
            await self._wait_for_file(self._started_path)

            t0 = time.monotonic()
            text = jobs.get_status_text(job_id_box.get("id"))
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 0.5, f"status blocked for {elapsed:.3f}s")
            self.assertIn("running", text)
            self.assertIn("blocked-in-worker", text)

            with open(self._release_path, "w", encoding="utf-8") as handle:
                handle.write("1")
            envelope = await asyncio.wait_for(task, timeout=5.0)
            self.assertIn("done", envelope)

    async def test_tools_are_coroutines(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(mcp_mod.cursor_ask))
        self.assertTrue(inspect.iscoroutinefunction(mcp_mod.cursor_plan))
        self.assertTrue(inspect.iscoroutinefunction(mcp_mod.cursor_implement))
        self.assertTrue(inspect.iscoroutinefunction(mcp_mod.cursor_cloud_plan))
        self.assertTrue(inspect.iscoroutinefunction(mcp_mod.cursor_cloud_review))
        self.assertTrue(inspect.iscoroutinefunction(mcp_mod.cursor_cloud_implement))


if __name__ == "__main__":
    unittest.main()
