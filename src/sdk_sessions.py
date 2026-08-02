"""Persist cursor-sdk agent IDs per workspace for continue_session."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_DEFAULT_SESSION_DIR = Path.home() / ".cache" / "cursor-headless" / "sdk-sessions"
_SESSION_DIR_ENV = "CURSOR_HEADLESS_SDK_SESSION_DIR"


def session_dir() -> Path:
    raw = os.environ.get(_SESSION_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_SESSION_DIR


def _session_file(workspace: str, mode: str) -> Path:
    digest = hashlib.sha256(f"{workspace}\0{mode}".encode("utf-8")).hexdigest()
    return session_dir() / f"{digest}.json"


def load_stored_agent_id(*, workspace: str, mode: str) -> str | None:
    path = _session_file(workspace, mode)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    agent_id = data.get("agent_id")
    if isinstance(agent_id, str) and agent_id.strip():
        return agent_id.strip()
    return None


def save_stored_agent_id(*, workspace: str, mode: str, agent_id: str) -> None:
    trimmed = agent_id.strip()
    if not trimmed:
        return
    directory = session_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_id": trimmed,
        "workspace": workspace,
        "mode": mode,
    }
    path = _session_file(workspace, mode)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
