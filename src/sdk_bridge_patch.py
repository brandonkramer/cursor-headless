"""Windows-safe patch for cursor-sdk Bridge discovery.

Upstream ``cursor_sdk._bridge._read_discovery`` uses ``selectors.select`` on a
process stderr fd. On Windows that raises WinError 10038 (not a socket).

A non-blocking ``os.read`` poll is also flaky here when Popen uses ``text=True``
(Codex MCP path often timed out with \"Timed out waiting for bridge discovery\"
even though the bridge prints ``cursor-sdk-bridge ready`` quickly).

Fix: background thread + blocking ``readline()``, joined with a deadline.
"""

from __future__ import annotations

import os
import sys
import threading
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

        # Allow longer discovery under slow antivirus / Codex spawn.
        try:
            override = float(os.environ.get("CURSOR_HEADLESS_SDK_BRIDGE_TIMEOUT", "") or "")
        except ValueError:
            override = 0.0
        if override > 0:
            timeout = override
        elif timeout < 60:
            timeout = 60.0

        stderr_lines: list[str] = []
        result: dict[str, Any] = {"discovery": None, "error": None}
        done = threading.Event()

        def reader() -> None:
            try:
                assert process.stderr is not None
                while not done.is_set():
                    line = process.stderr.readline()
                    if not line:
                        if process.poll() is not None:
                            return
                        time.sleep(0.01)
                        continue
                    stderr_lines.append(line)
                    discovery = parse_discovery_line(line)
                    if discovery is not None:
                        result["discovery"] = discovery
                        return
            except Exception as exc:  # noqa: BLE001 — surface to waiter
                result["error"] = exc

        thread = threading.Thread(target=reader, name="cursor-sdk-bridge-discovery", daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if result["discovery"] is not None:
                    return result["discovery"]  # type: ignore[return-value]
                if result["error"] is not None:
                    raise CursorSDKError(
                        f"Bridge discovery reader failed: {result['error']}"
                    )
                if not thread.is_alive() and result["discovery"] is None:
                    exit_code = process.poll()
                    raise CursorSDKError(
                        f"Bridge exited before discovery with status {exit_code}: "
                        + "".join(stderr_lines[-20:])
                    )
                # Wait briefly for the reader; also watch process death.
                thread.join(timeout=0.1)
                if result["discovery"] is not None:
                    return result["discovery"]  # type: ignore[return-value]
            raise CursorSDKError(
                "Timed out waiting for bridge discovery; stderr tail: "
                + ("".join(stderr_lines[-20:]) or "(empty)")
            )
        finally:
            done.set()
            thread.join(timeout=1.0)

    bridge._read_discovery = _read_discovery_windows  # type: ignore[assignment]
    bridge._cursor_headless_discovery_patched = True  # type: ignore[attr-defined]
    _PATCHED = True
    return True
