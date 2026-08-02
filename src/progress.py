"""Parse cursor-agent stream-json NDJSON into rate-limited progress events."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal, TypedDict

ProgressKind = Literal["init", "assistant_seg", "tool", "heartbeat", "result", "error"]
ToolPhase = Literal["started", "completed"]

_PATH_MAX_LEN = 120
_ASSISTANT_CHAR_BATCH = 500
_HEARTBEAT_INTERVAL_S = 15.0
_MAX_OUTBOUND_MESSAGE_LEN = 160
_MAX_SUMMARY_LINES = 8


class ProgressOutbound(TypedDict):
    progress: float
    message: str


@dataclass(frozen=True)
class ProgressEvent:
    kind: ProgressKind
    session_id: str | None = None
    model: str | None = None
    chars: int = 0
    phase: ToolPhase | None = None
    name: str = ""
    detail: str = ""
    elapsed_s: float = 0.0
    tools: int = 0
    last: str = ""
    ok: bool = True
    duration_ms: int | None = None
    result_text: str = ""
    message: str = ""


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _truncate_detail(text: str, *, max_len: int = _PATH_MAX_LEN) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _extract_assistant_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


def _assistant_is_partial_delta(raw: dict[str, object]) -> bool:
    """Partial-output delta: timestamp_ms present, model_call_id absent."""
    if raw.get("model_call_id") is not None:
        return False
    return raw.get("timestamp_ms") is not None


def _tool_label_and_detail(tool_call: dict[str, object]) -> tuple[str, str]:
    for key, label in (
        ("readToolCall", "read"),
        ("writeToolCall", "write"),
        ("editToolCall", "write"),
        ("shellToolCall", "shell"),
        ("grepToolCall", "grep"),
        ("globToolCall", "glob"),
        ("deleteToolCall", "delete"),
        ("listToolCall", "list"),
        ("searchToolCall", "search"),
    ):
        detail_obj = tool_call.get(key)
        if not isinstance(detail_obj, dict):
            continue
        args = detail_obj.get("args")
        path = ""
        if isinstance(args, dict):
            for arg_key in ("path", "command", "pattern", "glob_pattern"):
                raw_path = args.get(arg_key)
                if isinstance(raw_path, str) and raw_path.strip():
                    path = raw_path.strip()
                    break
        if path:
            return label, _truncate_detail(path)
        return label, ""

    fn = tool_call.get("function")
    if isinstance(fn, dict):
        name = fn.get("name")
        if isinstance(name, str) and name.strip():
            return "function", _truncate_detail(name.strip())
        args = fn.get("args")
        if isinstance(args, dict):
            for arg_key in ("path", "command", "pattern"):
                raw_path = args.get(arg_key)
                if isinstance(raw_path, str) and raw_path.strip():
                    return "function", _truncate_detail(raw_path.strip())

    mcp = tool_call.get("mcpToolCall")
    if isinstance(mcp, dict):
        name = mcp.get("name") or mcp.get("toolName")
        if isinstance(name, str) and name.strip():
            return "function", _truncate_detail(name.strip())

    return "tool", ""


def _parse_init(raw: dict[str, object]) -> ProgressEvent | None:
    if raw.get("type") != "system" or raw.get("subtype") != "init":
        return None
    return ProgressEvent(
        kind="init",
        session_id=_as_str(raw.get("session_id")),
        model=_as_str(raw.get("model")),
    )


def _parse_assistant(raw: dict[str, object]) -> ProgressEvent | None:
    if raw.get("type") != "assistant":
        return None
    if not _assistant_is_partial_delta(raw):
        return None
    text = _extract_assistant_text(raw.get("message"))
    if not text:
        return None
    return ProgressEvent(kind="assistant_seg", chars=len(text))


def _parse_tool(raw: dict[str, object]) -> ProgressEvent | None:
    if raw.get("type") != "tool_call":
        return None
    subtype = raw.get("subtype")
    if subtype not in ("started", "completed"):
        return None
    tool_call = raw.get("tool_call")
    if not isinstance(tool_call, dict):
        return ProgressEvent(kind="tool", phase=subtype, name="tool")
    name, detail = _tool_label_and_detail(tool_call)
    return ProgressEvent(kind="tool", phase=subtype, name=name, detail=detail)


def _parse_result(raw: dict[str, object]) -> ProgressEvent | None:
    if raw.get("type") != "result":
        return None
    subtype = _as_str(raw.get("subtype"))
    is_error = raw.get("is_error")
    ok = not bool(is_error)
    if subtype in ("error", "failure"):
        ok = False
    elif subtype == "success":
        ok = True
    result_text = _as_str(raw.get("result")) or ""
    duration_ms = _as_int(raw.get("duration_ms"))
    return ProgressEvent(
        kind="result",
        ok=ok,
        duration_ms=duration_ms,
        result_text=result_text,
    )


def _parse_error(raw: dict[str, object]) -> ProgressEvent | None:
    etype = raw.get("type")
    if etype == "error":
        msg = _as_str(raw.get("message")) or _as_str(raw.get("error")) or "unknown error"
        return ProgressEvent(kind="error", message=msg, ok=False)
    return None


def parse_stream_json_line(line: str) -> ProgressEvent | None:
    """Parse one NDJSON line from cursor-agent stream-json output."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    for parser in (_parse_init, _parse_assistant, _parse_tool, _parse_result, _parse_error):
        event = parser(parsed)
        if event is not None:
            return event
    return None


def _compact_message(text: str) -> str:
    return _truncate_detail(text, max_len=_MAX_OUTBOUND_MESSAGE_LEN)


@dataclass
class ProgressAggregator:
    """Rate-limit parsed events into MCP-friendly progress notifications."""

    _start_mono: float = field(default_factory=time.monotonic)
    _progress: float = 0.0
    _tools_started: int = 0
    _tools_completed: int = 0
    _last_tool_label: str = ""
    _assistant_pending_chars: int = 0
    _assistant_total_chars: int = 0
    _last_heartbeat_mono: float = field(default_factory=time.monotonic)
    _summary_lines: list[str] = field(default_factory=list)
    _phase: str = "init"
    _session_id: str | None = None
    _model: str | None = None
    _result_ok: bool | None = None
    _duration_ms: int | None = None
    _error_message: str = ""

    def feed(self, event: ProgressEvent | None) -> list[ProgressOutbound]:
        outbound: list[ProgressOutbound] = []
        if event is not None:
            outbound.extend(self._consume(event))
        outbound.extend(self._maybe_heartbeat(force=False))
        return outbound

    def poll(self) -> list[ProgressOutbound]:
        """Emit a wall-clock heartbeat when the interval elapses."""
        return self._maybe_heartbeat(force=False)

    def summary_lines(self) -> list[str]:
        return list(self._summary_lines[-_MAX_SUMMARY_LINES:])

    def to_status_dict(self) -> dict[str, object]:
        elapsed = max(0.0, time.monotonic() - self._start_mono)
        message = self._summary_lines[-1] if self._summary_lines else self._phase
        status: dict[str, object] = {
            "phase": self._phase,
            "message": message,
            "tool": self._last_tool_label or None,
            "elapsed_sec": round(elapsed, 1),
            "progress": self._progress,
            "tools": self._tools_started,
        }
        if self._model:
            status["model"] = self._model
        if self._session_id:
            status["session_id"] = self._session_id
        if self._result_ok is not None:
            status["ok"] = self._result_ok
        if self._duration_ms is not None:
            status["duration_ms"] = self._duration_ms
        if self._error_message:
            status["error"] = self._error_message
        return status

    def _append_summary(self, line: str) -> None:
        self._summary_lines.append(_compact_message(line))
        if len(self._summary_lines) > _MAX_SUMMARY_LINES:
            del self._summary_lines[: -_MAX_SUMMARY_LINES]

    def _emit(self, message: str) -> ProgressOutbound:
        self._progress += 1.0
        compact = _compact_message(message)
        self._append_summary(compact)
        return ProgressOutbound(progress=self._progress, message=compact)

    def _elapsed_s(self) -> float:
        return max(0.0, time.monotonic() - self._start_mono)

    def _heartbeat_message(self) -> str:
        last = self._last_tool_label or self._phase
        return (
            f"t={int(self._elapsed_s())}s "
            f"tools={self._tools_started} "
            f"last={last}"
        )

    def _maybe_heartbeat(self, *, force: bool) -> list[ProgressOutbound]:
        now = time.monotonic()
        if not force and (now - self._last_heartbeat_mono) < _HEARTBEAT_INTERVAL_S:
            return []
        self._last_heartbeat_mono = now
        return [self._emit(self._heartbeat_message())]

    def _flush_assistant(self) -> list[ProgressOutbound]:
        if self._assistant_pending_chars <= 0:
            return []
        pending = self._assistant_pending_chars
        self._assistant_pending_chars = 0
        return [
            self._emit(
                f"assistant +{pending} chars ({self._assistant_total_chars} total)"
            )
        ]

    def _consume(self, event: ProgressEvent) -> list[ProgressOutbound]:
        if event.kind == "init":
            return self._handle_init(event)
        if event.kind == "assistant_seg":
            return self._handle_assistant(event)
        if event.kind == "tool":
            return self._handle_tool(event)
        if event.kind == "result":
            return self._handle_result(event)
        if event.kind == "error":
            return self._handle_error(event)
        if event.kind == "heartbeat":
            return [self._emit(event.message or self._heartbeat_message())]
        return []

    def _handle_init(self, event: ProgressEvent) -> list[ProgressOutbound]:
        self._phase = "running"
        if event.session_id:
            self._session_id = event.session_id
        if event.model:
            self._model = event.model
        model_label = event.model or "unknown"
        return [self._emit(f"init model={model_label}")]

    def _handle_assistant(self, event: ProgressEvent) -> list[ProgressOutbound]:
        if event.chars <= 0:
            return []
        self._assistant_pending_chars += event.chars
        self._assistant_total_chars += event.chars
        if self._assistant_pending_chars < _ASSISTANT_CHAR_BATCH:
            return []
        return self._flush_assistant()

    def _handle_tool(self, event: ProgressEvent) -> list[ProgressOutbound]:
        outbound: list[ProgressOutbound] = []
        outbound.extend(self._flush_assistant())

        label = event.name or "tool"
        if event.detail:
            self._last_tool_label = f"{label} {event.detail}"
        else:
            self._last_tool_label = label

        if event.phase == "started":
            self._tools_started += 1
            detail = f" {event.detail}" if event.detail else ""
            outbound.append(
                self._emit(f"tool #{self._tools_started} {label}{detail}".strip())
            )
        elif event.phase == "completed":
            self._tools_completed += 1
            detail = f" {event.detail}" if event.detail else ""
            outbound.append(self._emit(f"tool done {label}{detail}".strip()))

        outbound.extend(self._maybe_heartbeat(force=True))
        return outbound

    def _handle_result(self, event: ProgressEvent) -> list[ProgressOutbound]:
        outbound = self._flush_assistant()
        self._phase = "done" if event.ok else "error"
        self._result_ok = event.ok
        self._duration_ms = event.duration_ms
        if not event.ok:
            self._error_message = event.result_text or "run failed"
        dur = f" {event.duration_ms}ms" if event.duration_ms is not None else ""
        status = "ok" if event.ok else "error"
        outbound.append(self._emit(f"result {status}{dur}".strip()))
        return outbound

    def _handle_error(self, event: ProgressEvent) -> list[ProgressOutbound]:
        outbound = self._flush_assistant()
        self._phase = "error"
        self._result_ok = False
        self._error_message = event.message
        outbound.append(self._emit(f"error: {event.message}"))
        return outbound
