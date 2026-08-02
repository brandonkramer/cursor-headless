"""Windows-safe patch for cursor-sdk Bridge discovery.

Upstream ``cursor_sdk._bridge._read_discovery`` uses ``selectors.select`` on a
process stderr fd. On Windows that raises WinError 10038 (not a socket).

We keep the same non-blocking drain loop and replace ``select`` with a short
sleep poll. No-op on non-Windows or if the hook is already applied.
"""

from __future__ import annotations

import codecs
import os
import sys
import time
from collections.abc import Mapping
from typing import Any

_PATCHED = False


def apply_windows_bridge_discovery_patch() -> bool:
    """Patch cursor_sdk bridge discovery for Windows. Returns True if applied."""
    global _PATCHED
    if _PATCHED or os.name != "nt":
        return False

    bridge = sys.modules.get("cursor_sdk._bridge")
    if bridge is None:
        try:
            import cursor_sdk._bridge as bridge  # noqa: PLC0415
        except ImportError:
            return False

    if getattr(bridge, "_cursor_headless_discovery_patched", False):
        _PATCHED = True
        return False

    parse_discovery_line = bridge.parse_discovery_line
    CursorSDKError = bridge.CursorSDKError

    def _read_discovery_windows(
        process: Any,
        timeout: float,
    ) -> Mapping[str, Any]:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")
        stderr_fd = process.stderr.fileno()
        was_blocking = os.get_blocking(stderr_fd)
        os.set_blocking(stderr_fd, False)
        try:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            deadline = time.monotonic() + timeout
            stderr_lines: list[str] = []
            pending = ""

            def drain_available() -> Mapping[str, Any] | None:
                nonlocal pending
                while True:
                    try:
                        chunk = os.read(stderr_fd, 8192)
                    except BlockingIOError:
                        return None
                    if not chunk:
                        final_text = decoder.decode(b"", final=True)
                        if final_text:
                            pending += final_text
                        if pending:
                            line = pending
                            pending = ""
                            stderr_lines.append(line)
                            return parse_discovery_line(line)
                        return None
                    pending += decoder.decode(chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line += "\n"
                        stderr_lines.append(line)
                        discovery = parse_discovery_line(line)
                        if discovery is not None:
                            return discovery

            while time.monotonic() < deadline:
                discovery = drain_available()
                if discovery is not None:
                    return discovery
                exit_code = process.poll()
                if exit_code is not None:
                    discovery = drain_available()
                    if discovery is not None:
                        return discovery
                    raise CursorSDKError(
                        f"Bridge exited before discovery with status {exit_code}: "
                        + "".join(stderr_lines)
                        + pending
                    )
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            raise CursorSDKError("Timed out waiting for bridge discovery")
        finally:
            os.set_blocking(stderr_fd, was_blocking)

    bridge._read_discovery = _read_discovery_windows  # type: ignore[assignment]
    bridge._cursor_headless_discovery_patched = True  # type: ignore[attr-defined]
    _PATCHED = True
    return True
