"""Map PR review comments onto commentable diff lines from GitHub file patches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@"
)


@dataclass(frozen=True)
class FileDiffMap:
    """Commentable line numbers and hunk membership per diff side."""

    left_lines: frozenset[int]
    right_lines: frozenset[int]
    left_hunk: frozenset[tuple[int, int]]  # (line, hunk_index)
    right_hunk: frozenset[tuple[int, int]]


class ReviewCommentLike(Protocol):
    path: str
    body: str
    line: int
    side: str
    start_line: int | None
    start_side: str | None
    subject_type: str


def parse_patch(patch: str) -> FileDiffMap:
    """Parse a unified diff patch into commentable LEFT/RIGHT line numbers."""
    left_lines: set[int] = set()
    right_lines: set[int] = set()
    left_hunk: set[tuple[int, int]] = set()
    right_hunk: set[tuple[int, int]] = set()

    old_line = 0
    new_line = 0
    hunk_index = -1

    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = _HUNK_HEADER_RE.match(raw)
            if match:
                old_line = int(match.group("old_start"))
                new_line = int(match.group("new_start"))
                hunk_index += 1
            continue

        if not raw:
            continue

        prefix = raw[0]
        if prefix == " ":
            left_lines.add(old_line)
            right_lines.add(new_line)
            left_hunk.add((old_line, hunk_index))
            right_hunk.add((new_line, hunk_index))
            old_line += 1
            new_line += 1
        elif prefix == "-":
            left_lines.add(old_line)
            left_hunk.add((old_line, hunk_index))
            old_line += 1
        elif prefix == "+":
            right_lines.add(new_line)
            right_hunk.add((new_line, hunk_index))
            new_line += 1
        # "\\ No newline at end of file" and other markers are ignored.

    return FileDiffMap(
        left_lines=frozenset(left_lines),
        right_lines=frozenset(right_lines),
        left_hunk=frozenset(left_hunk),
        right_hunk=frozenset(right_hunk),
    )


def _side_lines(diff: FileDiffMap, side: str) -> frozenset[int]:
    return diff.left_lines if side == "LEFT" else diff.right_lines


def _side_hunk(diff: FileDiffMap, side: str) -> frozenset[tuple[int, int]]:
    return diff.left_hunk if side == "LEFT" else diff.right_hunk


def _hunk_index(diff: FileDiffMap, side: str, line: int) -> int | None:
    for ln, idx in _side_hunk(diff, side):
        if ln == line:
            return idx
    return None


def _line_commentable(diff: FileDiffMap, *, side: str, line: int) -> bool:
    return line in _side_lines(diff, side)


def _same_hunk(diff: FileDiffMap, *, side: str, start: int, end: int) -> bool:
    start_idx = _hunk_index(diff, side, start)
    end_idx = _hunk_index(diff, side, end)
    if start_idx is None or end_idx is None:
        return False
    return start_idx == end_idx


def map_comment_to_payload(
    comment: ReviewCommentLike,
    file_maps: dict[str, FileDiffMap],
) -> tuple[dict[str, Any] | None, str | None]:
    """Map a proposed comment to a GitHub review comment payload, or reject it."""
    path = comment.path.strip()
    body = comment.body.strip()
    if not path or not body:
        return None, "missing path or body"

    subject_type = (comment.subject_type or "line").strip().lower()
    if subject_type == "file":
        if path not in file_maps:
            return None, f"{path}: file not in PR diff"
        return {"path": path, "body": body, "subject_type": "file"}, None

    line = comment.line
    if line <= 0:
        return None, f"{path}: missing or invalid line"

    side = (comment.side or "RIGHT").strip().upper()
    if side not in {"LEFT", "RIGHT"}:
        side = "RIGHT"

    diff = file_maps.get(path)
    if diff is None:
        return None, f"{path}: file not in PR diff"

    if not _line_commentable(diff, side=side, line=line):
        return None, f"{path}:{line} ({side}): line not in diff"

    payload: dict[str, Any] = {"path": path, "line": line, "side": side, "body": body}

    start_line = comment.start_line
    start_side_raw = comment.start_side
    if start_line is not None:
        if start_line <= 0:
            return None, f"{path}:{line} ({side}): invalid start_line"
        start_side = (start_side_raw or side).strip().upper()
        if start_side not in {"LEFT", "RIGHT"}:
            start_side = side
        if start_side != side:
            return None, (
                f"{path}:{start_line}-{line}: start_side ({start_side}) "
                f"must match side ({side})"
            )
        if start_line > line:
            return None, f"{path}:{start_line}-{line} ({side}): start_line > line"
        if not _line_commentable(diff, side=side, line=start_line):
            return None, f"{path}:{start_line} ({side}): start_line not in diff"
        if not _same_hunk(diff, side=side, start=start_line, end=line):
            return None, (
                f"{path}:{start_line}-{line} ({side}): range spans multiple hunks"
            )
        payload["start_line"] = start_line
        payload["start_side"] = start_side

    return payload, None


def filter_comments_to_diff(
    comments: list[ReviewCommentLike],
    file_maps: dict[str, FileDiffMap],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep only comments that land on commentable diff lines."""
    valid: list[dict[str, Any]] = []
    dropped: list[str] = []
    for comment in comments:
        payload, reason = map_comment_to_payload(comment, file_maps)
        if payload is not None:
            valid.append(payload)
        elif reason:
            dropped.append(reason)
    return valid, dropped
