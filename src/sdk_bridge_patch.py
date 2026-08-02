"""Windows-safe patch for cursor-sdk Bridge under MCP hosts.

Upstream issues on Windows:

1. ``_read_discovery`` uses ``selectors.select`` on a pipe → WinError 10038.
2. ``Bridge.launch`` uses ``Popen(..., stdin=None)`` so the bridge inherits the
   parent stdin. Under Codex/Claude MCP that stdin is the JSON-RPC pipe; the
   bridge then hangs and never prints ``cursor-sdk-bridge ready`` (empty stderr
   until discovery timeout). Interactive / closed-stdin runs still work.

Fixes:

- Threaded blocking ``readline()`` discovery (no selectors).
- Wrap ``Bridge.launch`` so bridge ``Popen`` uses ``stdin=DEVNULL`` and
  ``CREATE_NO_WINDOW``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any

_PATCHED = False


def apply_windows_bridge_discovery_patch() -> bool:
    """Patch cursor_sdk bridge launch + discovery for Windows. Returns True if applied."""
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
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _read_discovery_windows(
        process: Any,
        timeout: float,
    ) -> Mapping[str, Any]:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")

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
                thread.join(timeout=0.1)
                if result["discovery"] is not None:
                    return result["discovery"]  # type: ignore[return-value]
            raise CursorSDKError(
                "Timed out waiting for bridge discovery; "
                f"pid={getattr(process, 'pid', None)} poll={process.poll()}; "
                "stderr tail: "
                + ("".join(stderr_lines[-20:]) or "(empty)")
            )
        finally:
            done.set()
            thread.join(timeout=1.0)

    original_popen = bridge.subprocess.Popen  # type: ignore[attr-defined]

    def _popen_no_inherit_stdin(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        if create_no_window:
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | create_no_window
        return original_popen(*args, **kwargs)

    bridge.subprocess.Popen = _popen_no_inherit_stdin  # type: ignore[attr-defined]
    bridge._read_discovery = _read_discovery_windows  # type: ignore[assignment]
    bridge._cursor_headless_discovery_patched = True  # type: ignore[attr-defined]
    _PATCHED = True
    return True
