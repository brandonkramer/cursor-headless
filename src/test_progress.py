"""Unit tests for progress.py stream-json parsing and aggregation."""

from __future__ import annotations

import json
import time
import unittest

import progress


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj, separators=(",", ":"))


INIT_LINE = _line(
    {
        "type": "system",
        "subtype": "init",
        "cwd": "/tmp/ws",
        "session_id": "sess-abc",
        "model": "composer-2.5",
    }
)

ASSISTANT_DELTA = _line(
    {
        "type": "assistant",
        "timestamp_ms": 1785705942088,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello "}],
        },
        "session_id": "sess-abc",
    }
)

ASSISTANT_DUPLICATE_FLUSH = _line(
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello world"}],
        },
        "session_id": "sess-abc",
    }
)

ASSISTANT_WITH_MODEL_CALL_ID = _line(
    {
        "type": "assistant",
        "timestamp_ms": 1785705942099,
        "model_call_id": "call-123",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "skip me"}],
        },
    }
)

TOOL_READ_STARTED = _line(
    {
        "type": "tool_call",
        "subtype": "started",
        "tool_call": {
            "readToolCall": {
                "args": {"path": "/very/long/path/" + ("x" * 200) + "/file.ts"},
            }
        },
        "timestamp_ms": 1785705943000,
    }
)

TOOL_READ_COMPLETED = _line(
    {
        "type": "tool_call",
        "subtype": "completed",
        "tool_call": {
            "readToolCall": {
                "args": {"path": "src/foo.ts"},
                "result": {
                    "success": {
                        "content": "file contents must never appear in detail",
                        "path": "src/foo.ts",
                    }
                },
            }
        },
        "timestamp_ms": 1785705944000,
    }
)

TOOL_FUNCTION_STARTED = _line(
    {
        "type": "tool_call",
        "subtype": "started",
        "tool_call": {
            "function": {"name": "grep", "args": {"pattern": "ProgressEvent"}},
        },
    }
)

RESULT_OK = _line(
    {
        "type": "result",
        "subtype": "success",
        "duration_ms": 12345,
        "is_error": False,
        "result": "All done.",
    }
)

RESULT_ERROR = _line(
    {
        "type": "result",
        "subtype": "error",
        "duration_ms": 500,
        "is_error": True,
        "result": "Something failed.",
    }
)


class ParseStreamJsonLineTests(unittest.TestCase):
    def test_init_event(self) -> None:
        event = progress.parse_stream_json_line(INIT_LINE)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "init")
        self.assertEqual(event.session_id, "sess-abc")
        self.assertEqual(event.model, "composer-2.5")

    def test_assistant_delta_accepted(self) -> None:
        event = progress.parse_stream_json_line(ASSISTANT_DELTA)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "assistant_seg")
        self.assertEqual(event.chars, 6)

    def test_assistant_duplicate_flush_skipped(self) -> None:
        self.assertIsNone(progress.parse_stream_json_line(ASSISTANT_DUPLICATE_FLUSH))

    def test_assistant_with_model_call_id_skipped(self) -> None:
        self.assertIsNone(progress.parse_stream_json_line(ASSISTANT_WITH_MODEL_CALL_ID))

    def test_tool_read_started_truncates_path(self) -> None:
        event = progress.parse_stream_json_line(TOOL_READ_STARTED)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "tool")
        self.assertEqual(event.phase, "started")
        self.assertEqual(event.name, "read")
        self.assertLessEqual(len(event.detail), 120)
        self.assertTrue(event.detail.endswith("..."))
        self.assertNotIn("file contents", event.detail)

    def test_tool_completed_uses_args_not_result_body(self) -> None:
        event = progress.parse_stream_json_line(TOOL_READ_COMPLETED)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.phase, "completed")
        self.assertEqual(event.detail, "src/foo.ts")
        self.assertNotIn("file contents", event.detail)

    def test_tool_function_name(self) -> None:
        event = progress.parse_stream_json_line(TOOL_FUNCTION_STARTED)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.name, "function")
        self.assertEqual(event.detail, "grep")

    def test_result_ok(self) -> None:
        event = progress.parse_stream_json_line(RESULT_OK)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "result")
        self.assertTrue(event.ok)
        self.assertEqual(event.duration_ms, 12345)
        self.assertEqual(event.result_text, "All done.")

    def test_result_error(self) -> None:
        event = progress.parse_stream_json_line(RESULT_ERROR)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertFalse(event.ok)

    def test_malformed_and_unknown_ignored(self) -> None:
        self.assertIsNone(progress.parse_stream_json_line(""))
        self.assertIsNone(progress.parse_stream_json_line("not json"))
        self.assertIsNone(progress.parse_stream_json_line('{"type":"thinking","subtype":"delta"}'))
        self.assertIsNone(progress.parse_stream_json_line('{"type":"user","message":{}}'))
        self.assertIsNone(progress.parse_stream_json_line("[1,2,3]"))


class ProgressAggregatorTests(unittest.TestCase):
    def test_ndjson_sequence_init_tool_result(self) -> None:
        agg = progress.ProgressAggregator()
        lines = [
            INIT_LINE,
            TOOL_READ_STARTED,
            TOOL_READ_COMPLETED,
            RESULT_OK,
        ]
        outbound: list[progress.ProgressOutbound] = []
        for line in lines:
            event = progress.parse_stream_json_line(line)
            outbound.extend(agg.feed(event))

        self.assertGreater(len(outbound), 0)
        self.assertEqual(outbound[0]["message"], "init model=composer-2.5")
        self.assertTrue(any("tool #1 read" in msg["message"] for msg in outbound))
        self.assertTrue(any(msg["message"].startswith("result ok") for msg in outbound))

        progress_values = [msg["progress"] for msg in outbound]
        self.assertEqual(progress_values, sorted(progress_values))
        self.assertNotEqual(progress_values[0], progress_values[-1])

        status = agg.to_status_dict()
        self.assertEqual(status["phase"], "done")
        self.assertEqual(status["ok"], True)
        self.assertEqual(status["duration_ms"], 12345)
        self.assertEqual(status["model"], "composer-2.5")
        self.assertGreaterEqual(int(status["tools"]), 1)

        summary = agg.summary_lines()
        self.assertLessEqual(len(summary), 8)
        self.assertGreater(len(summary), 0)

    def test_assistant_batching_at_500_chars(self) -> None:
        agg = progress.ProgressAggregator()
        chunk = "a" * 250
        delta = _line(
            {
                "type": "assistant",
                "timestamp_ms": 1,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": chunk}],
                },
            }
        )
        first = agg.feed(progress.parse_stream_json_line(delta))
        self.assertEqual(first, [])

        second = agg.feed(progress.parse_stream_json_line(delta))
        self.assertEqual(len(second), 1)
        self.assertIn("assistant +500 chars", second[0]["message"])

    def test_tool_boundary_flushes_assistant(self) -> None:
        agg = progress.ProgressAggregator()
        delta = _line(
            {
                "type": "assistant",
                "timestamp_ms": 1,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "partial text"}],
                },
            }
        )
        agg.feed(progress.parse_stream_json_line(delta))
        flushed = agg.feed(progress.parse_stream_json_line(TOOL_READ_STARTED))
        self.assertTrue(any("assistant +" in msg["message"] for msg in flushed))
        self.assertTrue(any("tool #1 read" in msg["message"] for msg in flushed))

    def test_tool_triggers_heartbeat(self) -> None:
        agg = progress.ProgressAggregator()
        agg.feed(progress.parse_stream_json_line(INIT_LINE))
        before = agg.to_status_dict()["progress"]
        tool_msgs = agg.feed(progress.parse_stream_json_line(TOOL_READ_STARTED))
        self.assertTrue(
            any(msg["message"].startswith("t=") and "tools=" in msg["message"] for msg in tool_msgs)
        )
        after = agg.to_status_dict()["progress"]
        self.assertGreater(after, before)

    def test_poll_wall_clock_heartbeat(self) -> None:
        agg = progress.ProgressAggregator()
        agg._last_heartbeat_mono = time.monotonic() - 20.0  # noqa: SLF001
        polled = agg.poll()
        self.assertEqual(len(polled), 1)
        self.assertIn("tools=", polled[0]["message"])

    def test_poll_before_interval_is_quiet(self) -> None:
        agg = progress.ProgressAggregator()
        agg.feed(progress.parse_stream_json_line(INIT_LINE))
        self.assertEqual(agg.poll(), [])


if __name__ == "__main__":
    unittest.main()
