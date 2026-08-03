#!/usr/bin/env python3
"""Tests for PR diff line mapping."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_diff
import pr_review_publish

GREETER_PATCH = """@@ -1,7 +1,7 @@
 \"\"\"Tiny module...\"\"\"
 
 def greet(name: str) -> str:
-    return f\"hello, {name}\"
+    return f\"hi, {name}\"
"""


class ParsePatchTests(unittest.TestCase):
    def test_greeter_patch_commentable_lines(self) -> None:
        diff = pr_diff.parse_patch(GREETER_PATCH)
        self.assertIn(4, diff.right_lines)
        self.assertIn(4, diff.left_lines)
        self.assertNotIn(99, diff.right_lines)

    def test_context_lines_commentable_on_both_sides(self) -> None:
        diff = pr_diff.parse_patch(GREETER_PATCH)
        self.assertIn(3, diff.right_lines)
        self.assertIn(3, diff.left_lines)


class MapCommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.maps = {"src/greeter.py": pr_diff.parse_patch(GREETER_PATCH)}

    def _comment(
        self,
        *,
        line: int = 4,
        side: str = "RIGHT",
        start_line: int | None = None,
        start_side: str | None = None,
        subject_type: str = "line",
    ) -> pr_review_publish.InlineComment:
        return pr_review_publish.InlineComment(
            path="src/greeter.py",
            line=line,
            body="nit",
            side=side,
            start_line=start_line,
            start_side=start_side,
            subject_type=subject_type,  # type: ignore[arg-type]
        )

    def test_right_line_4_valid(self) -> None:
        payload, reason = pr_diff.map_comment_to_payload(
            self._comment(line=4, side="RIGHT"), self.maps
        )
        self.assertIsNone(reason)
        assert payload is not None
        self.assertEqual(payload["line"], 4)
        self.assertEqual(payload["side"], "RIGHT")

    def test_left_line_4_valid(self) -> None:
        payload, reason = pr_diff.map_comment_to_payload(
            self._comment(line=4, side="LEFT"), self.maps
        )
        self.assertIsNone(reason)
        assert payload is not None
        self.assertEqual(payload["side"], "LEFT")

    def test_off_diff_line_rejected(self) -> None:
        _, reason = pr_diff.map_comment_to_payload(
            self._comment(line=99, side="RIGHT"), self.maps
        )
        self.assertIn("not in diff", reason or "")

    def test_unknown_file_rejected(self) -> None:
        comment = pr_review_publish.InlineComment(
            path="missing.py", line=1, body="x"
        )
        _, reason = pr_diff.map_comment_to_payload(comment, self.maps)
        self.assertIn("not in PR diff", reason or "")

    def test_multiline_same_hunk(self) -> None:
        payload, reason = pr_diff.map_comment_to_payload(
            self._comment(line=4, side="RIGHT", start_line=3, start_side="RIGHT"),
            self.maps,
        )
        self.assertIsNone(reason)
        assert payload is not None
        self.assertEqual(payload["start_line"], 3)
        self.assertEqual(payload["start_side"], "RIGHT")

    def test_multiline_cross_hunk_rejected(self) -> None:
        patch = """@@ -1,2 +1,2 @@
 a
-b
+b
@@ -10,2 +10,2 @@
 c
-d
+d
"""
        maps = {"f.py": pr_diff.parse_patch(patch)}
        comment = pr_review_publish.InlineComment(
            path="f.py",
            line=11,
            body="range",
            side="RIGHT",
            start_line=2,
            start_side="RIGHT",
        )
        _, reason = pr_diff.map_comment_to_payload(comment, maps)
        self.assertIn("multiple hunks", reason or "")

    def test_file_level_comment(self) -> None:
        payload, reason = pr_diff.map_comment_to_payload(
            self._comment(subject_type="file", line=0), self.maps
        )
        self.assertIsNone(reason)
        assert payload is not None
        self.assertEqual(payload["subject_type"], "file")
        self.assertNotIn("line", payload)


class FilterCommentsTests(unittest.TestCase):
    def test_filter_comments_to_diff(self) -> None:
        pr = pr_review_publish.PrRef(
            owner="o", repo="r", number=1, url="https://github.com/o/r/pull/1"
        )
        maps = {"src/greeter.py": pr_diff.parse_patch(GREETER_PATCH)}
        comments = [
            pr_review_publish.InlineComment(
                path="src/greeter.py", line=4, body="ok", side="RIGHT"
            ),
            pr_review_publish.InlineComment(
                path="src/greeter.py", line=99, body="bad", side="RIGHT"
            ),
        ]
        valid, dropped = pr_review_publish.filter_comments_to_diff(
            pr, comments, file_maps=maps
        )
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["line"], 4)
        self.assertEqual(len(dropped), 1)
        self.assertIn("not in diff", dropped[0])


class PublishDiffFilterTests(unittest.TestCase):
    def test_publish_filters_before_post(self) -> None:
        pr_payload = {"head": {"sha": "abc123"}}
        review_payload = {"id": 1, "html_url": "https://github.com/o/r/pull/1#r1"}
        posted: list[dict[str, object]] = []

        def fake_gh(args: list[str], *, input_text: str | None = None):
            if args[:2] == ["api", "repos/o/r/pulls/1/files"]:
                return (
                    0,
                    [
                        {
                            "filename": "src/a.py",
                            "patch": "@@ -1,1 +1,1 @@\n-old\n+new",
                        }
                    ],
                    "",
                )
            if args[:2] == ["api", "repos/o/r/pulls/1"] and "--method" not in args:
                return 0, pr_payload, ""
            if "--method" in args and "POST" in args:
                assert input_text is not None
                body = json.loads(input_text)
                posted.append(body)
                self.assertEqual(len(body.get("comments", [])), 1)
                self.assertEqual(body["comments"][0]["line"], 1)
                return 0, review_payload, ""
            return 1, None, "unexpected"

        review_text = json.dumps(
            {
                "summary": "review",
                "comments": [
                    {"path": "src/a.py", "line": 1, "body": "good"},
                    {"path": "src/a.py", "line": 50, "body": "bad"},
                ],
            }
        )

        with patch.object(pr_review_publish, "_gh_json", side_effect=fake_gh):
            result = pr_review_publish.publish_pr_review(
                pr_url="https://github.com/o/r/pull/1",
                review_text=review_text,
            )
        self.assertTrue(result.ok)
        self.assertIn("dropped=1", result.message)

    def test_all_invalid_posts_summary_only(self) -> None:
        posted: list[dict[str, object]] = []

        def fake_gh(args: list[str], *, input_text: str | None = None):
            if args[:2] == ["api", "repos/o/r/pulls/1/files"]:
                return (
                    0,
                    [{"filename": "src/a.py", "patch": "@@ -1,1 +1,1 @@\n-old\n+new"}],
                    "",
                )
            if args[:2] == ["api", "repos/o/r/pulls/1"]:
                return 0, {"head": {"sha": "abc"}}, ""
            if "--method" in args:
                assert input_text is not None
                posted.append(json.loads(input_text))
                return 0, {"id": 2, "html_url": "https://github.com/o/r/pull/1#r2"}, ""
            return 1, None, "fail"

        with patch.object(pr_review_publish, "_gh_json", side_effect=fake_gh):
            result = pr_review_publish.publish_pr_review(
                pr_url="https://github.com/o/r/pull/1",
                review_text=json.dumps(
                    {
                        "summary": "only summary",
                        "comments": [
                            {"path": "src/a.py", "line": 99, "body": "off diff"},
                        ],
                    }
                ),
            )
        self.assertTrue(result.ok)
        self.assertNotIn("comments", posted[0])
        self.assertIn("Dropped inline comment", posted[0]["body"])
        self.assertIn("dropped=1", result.message)


if __name__ == "__main__":
    unittest.main()
