"""ProgressStore — job files + helpers for cursor_status MCP tool."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

JobState = Literal["running", "done", "error", "timeout"]

_VALID_STATES: frozenset[str] = frozenset({"running", "done", "error", "timeout"})


class StatusDict(TypedDict, total=False):
    """Minimal status shape; extended when progress.py lands."""

    phase: str
    message: str
    tool: str
    elapsed_sec: float


class StatusProvider(Protocol):
    """Duck-type for ProgressAggregator.to_status_dict() when progress.py lands."""

    def to_status_dict(self) -> dict[str, object]: ...


def job_dir() -> Path:
    return Path(
        os.environ.get(
            "CURSOR_HEADLESS_JOB_DIR",
            str(Path.home() / ".cache" / "cursor-headless" / "jobs"),
        )
    )


def create_job() -> str:
    return uuid.uuid4().hex


def job_path(job_id: str) -> Path:
    return job_dir() / f"{job_id}.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_job(job_id: str, payload: dict[str, object]) -> None:
    path = job_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{job_id}-",
        suffix=".tmp",
    )
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_job(job_id: str) -> dict[str, object] | None:
    path = job_path(job_id)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def update_job_from_status(
    job_id: str,
    status: dict[str, object],
    *,
    state: JobState,
) -> None:
    if state not in _VALID_STATES:
        msg = f"invalid job state: {state!r} (expected running|done|error|timeout)"
        raise ValueError(msg)

    existing = read_job(job_id)
    if existing is None:
        job: dict[str, object] = {
            "job_id": job_id,
            "created_at": _now_iso(),
        }
    else:
        job = dict(existing)

    prior_status = job.get("status")
    merged_status: dict[str, object] = (
        dict(prior_status) if isinstance(prior_status, dict) else {}
    )
    merged_status.update(status)

    job["job_id"] = job_id
    job["state"] = state
    job["updated_at"] = _now_iso()
    job["status"] = merged_status
    write_job(job_id, job)


def format_status_text(job: dict[str, object]) -> str:
    job_id = job.get("job_id", "?")
    state = job.get("state", "unknown")
    updated = job.get("updated_at", "")
    status = job.get("status")

    lines = [f"job {job_id} [{state}]"]
    if isinstance(updated, str) and updated:
        lines[0] += f" updated {updated}"

    if isinstance(status, dict) and status:
        detail_parts: list[str] = []
        for key in ("phase", "tool", "message", "elapsed_sec"):
            value = status.get(key)
            if value is not None:
                detail_parts.append(f"{key}={value}")
        for key, value in sorted(status.items()):
            if key in ("phase", "tool", "message", "elapsed_sec"):
                continue
            if value is not None:
                detail_parts.append(f"{key}={value}")
        if detail_parts:
            lines.append(" | ".join(detail_parts))

    created = job.get("created_at")
    if isinstance(created, str) and created and created != updated:
        lines.append(f"created {created}")

    return "\n".join(lines)


def find_latest_job_id() -> str | None:
    directory = job_dir()
    if not directory.is_dir():
        return None
    candidates = [p for p in directory.glob("*.json") if p.is_file()]
    if not candidates:
        return None
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return latest.stem


def cleanup_old_jobs(*, max_age_hours: float = 24) -> int:
    """Delete job files older than max_age_hours. Returns count removed (best-effort)."""
    directory = job_dir()
    if not directory.is_dir():
        return 0

    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    removed = 0
    for path in directory.glob("*.json"):
        if not path.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def get_status_text(job_id: str | None = None) -> str:
    resolved_id = job_id if job_id is not None else find_latest_job_id()
    if resolved_id is None:
        store = job_dir()
        return f"no jobs found in job store ({store})"

    job = read_job(resolved_id)
    if job is None:
        return f"job not found: {resolved_id}"
    return format_status_text(job)


def register_status_tool(mcp: FastMCP) -> None:
    @mcp.tool()
    def cursor_status(job_id: str | None = None) -> str:
        """Read local job progress from the job store (no cursor-agent invocation).

        Omit job_id to return the most recently updated job, if any.
        """
        return get_status_text(job_id)
