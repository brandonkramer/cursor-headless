"""Unit tests for jobs.py ProgressStore helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import jobs


class JobsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._previous_job_dir = os.environ.get("CURSOR_HEADLESS_JOB_DIR")
        os.environ["CURSOR_HEADLESS_JOB_DIR"] = self._tmpdir.name

    def tearDown(self) -> None:
        if self._previous_job_dir is None:
            os.environ.pop("CURSOR_HEADLESS_JOB_DIR", None)
        else:
            os.environ["CURSOR_HEADLESS_JOB_DIR"] = self._previous_job_dir
        self._tmpdir.cleanup()

    def test_create_write_read_roundtrip(self) -> None:
        job_id = jobs.create_job()
        self.assertEqual(len(job_id), 32)

        payload: dict[str, object] = {
            "job_id": job_id,
            "state": "running",
            "created_at": "2026-08-02T12:00:00+00:00",
            "updated_at": "2026-08-02T12:00:00+00:00",
            "status": {"phase": "start", "message": "boot"},
        }
        jobs.write_job(job_id, payload)

        expected_path = Path(self._tmpdir.name) / f"{job_id}.json"
        self.assertTrue(expected_path.is_file())
        self.assertEqual(jobs.job_path(job_id), expected_path)

        loaded = jobs.read_job(job_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["job_id"], job_id)
        self.assertEqual(loaded["status"], {"phase": "start", "message": "boot"})

    def test_read_job_missing_returns_none(self) -> None:
        self.assertIsNone(jobs.read_job("doesnotexist"))

    def test_update_job_from_status_merges_and_sets_state(self) -> None:
        job_id = jobs.create_job()
        jobs.update_job_from_status(
            job_id,
            {"phase": "plan", "message": "thinking"},
            state="running",
        )
        jobs.update_job_from_status(
            job_id,
            {"message": "done thinking", "elapsed_sec": 12.5},
            state="done",
        )

        job = jobs.read_job(job_id)
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job["state"], "done")
        self.assertIn("updated_at", job)
        status = job.get("status")
        self.assertIsInstance(status, dict)
        assert isinstance(status, dict)
        self.assertEqual(status.get("phase"), "plan")
        self.assertEqual(status.get("message"), "done thinking")
        self.assertEqual(status.get("elapsed_sec"), 12.5)

    def test_update_job_from_status_rejects_invalid_state(self) -> None:
        job_id = jobs.create_job()
        with self.assertRaises(ValueError):
            jobs.update_job_from_status(job_id, {}, state="bogus")  # type: ignore[arg-type]

    def test_format_status_text_compact(self) -> None:
        job: dict[str, object] = {
            "job_id": "abc123",
            "state": "running",
            "updated_at": "2026-08-02T12:01:00+00:00",
            "created_at": "2026-08-02T12:00:00+00:00",
            "status": {
                "phase": "implement",
                "tool": "cursor_implement",
                "message": "editing files",
            },
        }
        text = jobs.format_status_text(job)
        self.assertIn("job abc123 [running]", text)
        self.assertIn("phase=implement", text)
        self.assertIn("tool=cursor_implement", text)
        self.assertIn("message=editing files", text)
        self.assertIn("created 2026-08-02T12:00:00+00:00", text)

    def test_find_latest_job_id_by_mtime(self) -> None:
        older = jobs.create_job()
        newer = jobs.create_job()
        jobs.write_job(older, {"job_id": older, "state": "done"})
        jobs.write_job(newer, {"job_id": newer, "state": "running"})

        older_path = jobs.job_path(older)
        newer_path = jobs.job_path(newer)
        older_ts = newer_path.stat().st_mtime - 10
        os.utime(older_path, (older_ts, older_ts))

        self.assertEqual(jobs.find_latest_job_id(), newer)

    def test_get_status_text_latest_and_missing(self) -> None:
        self.assertIn("no jobs found", jobs.get_status_text(None))
        self.assertIn("job not found", jobs.get_status_text("missing"))

        job_id = jobs.create_job()
        jobs.update_job_from_status(job_id, {"phase": "ask"}, state="running")
        text = jobs.get_status_text(None)
        self.assertIn(job_id, text)
        self.assertIn("phase=ask", text)

    def test_cleanup_old_jobs(self) -> None:
        job_id = jobs.create_job()
        jobs.write_job(job_id, {"job_id": job_id, "state": "done"})
        path = jobs.job_path(job_id)
        old_ts = path.stat().st_mtime - (25 * 3600)
        os.utime(path, (old_ts, old_ts))

        removed = jobs.cleanup_old_jobs(max_age_hours=24)
        self.assertEqual(removed, 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
