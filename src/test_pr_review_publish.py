#!/usr/bin/env python3
"""Tests for GitHub PR review publishing helpers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_diff
import pr_review_publish

_SAMPLE_A_PY_PATCH = "\n".join(
    [
        "@@ -1,2 +1,3 @@",
        " line1",
        "-old2",
        "+new2",
        "+new3",
    ]
)


def _sample_file_maps() -> dict[str, pr_diff.FileDiffMap]:
    return {"src/a.py": pr_diff.parse_patch(_SAMPLE_A_PY_PATCH)}


class ReviewBodyMetaTests(unittest.TestCase):
    def test_format_token_usage(self) -> None:
        self.assertIsNone(pr_review_publish.format_token_usage(None))
        self.assertEqual(
            pr_review_publish.format_token_usage(
                {
                    "total_tokens": 12345,
                    "input_tokens": 8000,
                    "output_tokens": 4345,
                }
            ),
            "12,345 total (in 8,000 · out 4,345)",
        )

    def test_format_pr_review_body_header(self) -> None:
        body = pr_review_publish.format_pr_review_body(
            "Looks good overall.",
            pr_review_publish.ReviewPublishMeta(
                model="composer-2.5",
                elapsed_s=28.1,
                usage={"total_tokens": 100, "input_tokens": 60, "output_tokens": 40},
                agent_id="bc-abc",
                tools=3,
                cloud_env="vm",
                backend="sdk-cloud",
                job_id="job-1",
                event="COMMENT",
                inline_count=2,
            ),
        )
        self.assertTrue(body.startswith("## Cursor cloud PR review"))
        self.assertIn("<details>", body)
        self.assertIn("</details>", body)
        self.assertIn(
            "<summary><code>composer-2.5</code> · 28.1s · 100 tokens · 2 inline</summary>",
            body,
        )
        self.assertIn("**Model:** `composer-2.5`", body)
        self.assertIn("**Elapsed:** 28.1s", body)
        self.assertIn("**Tokens:** 100 total (in 60 · out 40)", body)
        self.assertIn("**Agent:** `bc-abc`", body)
        self.assertIn("Looks good overall.", body)

    def test_format_pr_review_body_tokens_missing(self) -> None:
        body = pr_review_publish.format_pr_review_body(
            "summary",
            pr_review_publish.ReviewPublishMeta(model="composer-2.5", elapsed_s=1),
        )
        self.assertIn("**Tokens:** _(not reported)_", body)
        self.assertIn("<summary><code>composer-2.5</code> · 1s · tokens n/a</summary>", body)


class ParseHelpersTests(unittest.TestCase):
    def test_parse_pr_url(self) -> None:
        ref = pr_review_publish.parse_pr_url(
            "https://github.com/brandonkramer/cursor-headless-cloud-smoke/pull/1"
        )
        assert ref is not None
        self.assertEqual(ref.owner, "brandonkramer")
        self.assertEqual(ref.repo, "cursor-headless-cloud-smoke")
        self.assertEqual(ref.number, 1)

    def test_parse_review_json_fence(self) -> None:
        text = """
Here you go:
```json
{
  "summary": "Looks fine",
  "event": "COMMENT",
  "comments": [
    {"path": "src/greeter.py", "line": 4, "body": "naming nit", "severity": "nit"}
  ]
}
```
"""
        parsed = pr_review_publish.parse_review_payload(text)
        self.assertEqual(parsed.summary, "Looks fine")
        self.assertEqual(len(parsed.comments), 1)
        self.assertEqual(parsed.comments[0].path, "src/greeter.py")
        self.assertIn("nit", parsed.comments[0].body)

    def test_parse_fallback_plain_text(self) -> None:
        parsed = pr_review_publish.parse_review_payload("just prose findings")
        self.assertEqual(parsed.summary, "just prose findings")
        self.assertEqual(parsed.comments, [])

    def test_parse_multiline_comment_fields(self) -> None:
        parsed = pr_review_publish.parse_review_payload(
            json.dumps(
                {
                    "summary": "range comment",
                    "comments": [
                        {
                            "path": "src/a.py",
                            "start_line": 10,
                            "start_side": "LEFT",
                            "line": 12,
                            "side": "RIGHT",
                            "body": "multi-line issue",
                        }
                    ],
                }
            )
        )
        comment = parsed.comments[0]
        self.assertEqual(comment.start_line, 10)
        self.assertEqual(comment.start_side, "LEFT")
        self.assertEqual(comment.line, 12)
        self.assertEqual(comment.side, "RIGHT")
        self.assertEqual(comment.subject_type, "line")

    def test_parse_file_subject_type_without_line(self) -> None:
        parsed = pr_review_publish.parse_review_payload(
            json.dumps(
                {
                    "summary": "file note",
                    "comments": [
                        {
                            "path": "README.md",
                            "subject_type": "file",
                            "body": "missing changelog entry",
                        }
                    ],
                }
            )
        )
        comment = parsed.comments[0]
        self.assertEqual(comment.subject_type, "file")
        self.assertEqual(comment.path, "README.md")
        self.assertEqual(comment.line, 0)
        self.assertEqual(comment.body, "missing changelog entry")

    def test_parse_suggestion_stored_separately(self) -> None:
        parsed = pr_review_publish.parse_review_payload(
            json.dumps(
                {
                    "summary": "suggested fix",
                    "comments": [
                        {
                            "path": "src/a.py",
                            "line": 4,
                            "body": "use constant",
                            "severity": "nit",
                            "suggestion": "MAX_RETRIES = 3",
                        }
                    ],
                }
            )
        )
        comment = parsed.comments[0]
        self.assertIn("nit", comment.body)
        self.assertNotIn("```suggestion", comment.body)
        self.assertEqual(comment.suggestion, "MAX_RETRIES = 3")

    def test_format_comment_body_for_github_suggestion(self) -> None:
        body = pr_review_publish.format_comment_body_for_github(
            "replace this",
            "fixed = True",
        )
        self.assertEqual(
            body,
            "replace this\n\n```suggestion\nfixed = True\n```",
        )

    def test_inline_comment_to_github_dict_multiline_and_file(self) -> None:
        multiline = pr_review_publish.inline_comment_to_github_dict(
            pr_review_publish.InlineComment(
                path="src/a.py",
                line=12,
                body="issue",
                side="RIGHT",
                start_line=10,
                start_side="RIGHT",
            )
        )
        self.assertEqual(
            multiline,
            {
                "path": "src/a.py",
                "body": "issue",
                "line": 12,
                "side": "RIGHT",
                "start_line": 10,
                "start_side": "RIGHT",
            },
        )

        file_note = pr_review_publish.inline_comment_to_github_dict(
            pr_review_publish.InlineComment(
                path="README.md",
                line=0,
                body="doc gap",
                subject_type="file",
            )
        )
        self.assertEqual(
            file_note,
            {
                "path": "README.md",
                "body": "doc gap",
                "subject_type": "file",
            },
        )

        with_suggestion = pr_review_publish.inline_comment_to_github_dict(
            pr_review_publish.InlineComment(
                path="src/a.py",
                line=4,
                body="rename",
                suggestion="new_name = old_name",
            )
        )
        self.assertIn("```suggestion", with_suggestion["body"])
        self.assertIn("new_name = old_name", with_suggestion["body"])


class DiffFilterTests(unittest.TestCase):
    def test_accepts_changed_right_line_rejects_out_of_diff(self) -> None:
        pr = pr_review_publish.PrRef(
            owner="o",
            repo="r",
            number=1,
            url="https://github.com/o/r/pull/1",
        )
        comments = [
            pr_review_publish.InlineComment(
                path="src/a.py",
                line=3,
                body="on diff",
                side="RIGHT",
            ),
            pr_review_publish.InlineComment(
                path="src/a.py",
                line=99,
                body="off diff",
                side="RIGHT",
            ),
        ]
        valid, dropped = pr_review_publish.filter_comments_to_diff(
            pr,
            comments,
            file_maps=_sample_file_maps(),
        )
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["path"], "src/a.py")
        self.assertEqual(valid[0]["line"], 3)
        self.assertTrue(any("99" in reason for reason in dropped))

    def test_multiline_start_line_in_payload(self) -> None:
        pr = pr_review_publish.PrRef(
            owner="o",
            repo="r",
            number=1,
            url="https://github.com/o/r/pull/1",
        )
        comments = [
            pr_review_publish.InlineComment(
                path="src/a.py",
                line=3,
                body="range",
                side="RIGHT",
                start_line=2,
                start_side="RIGHT",
            ),
        ]
        valid, dropped = pr_review_publish.filter_comments_to_diff(
            pr,
            comments,
            file_maps=_sample_file_maps(),
        )
        self.assertEqual(dropped, [])
        self.assertEqual(valid[0]["start_line"], 2)
        self.assertEqual(valid[0]["start_side"], "RIGHT")

    def test_file_subject_type_passes_without_line(self) -> None:
        pr = pr_review_publish.PrRef(
            owner="o",
            repo="r",
            number=1,
            url="https://github.com/o/r/pull/1",
        )
        comments = [
            pr_review_publish.InlineComment(
                path="src/a.py",
                line=0,
                body="file note",
                subject_type="file",
            ),
        ]
        valid, dropped = pr_review_publish.filter_comments_to_diff(
            pr,
            comments,
            file_maps=_sample_file_maps(),
        )
        self.assertEqual(dropped, [])
        self.assertEqual(valid[0]["subject_type"], "file")
        self.assertNotIn("line", valid[0])


class PublishTests(unittest.TestCase):
    def test_publish_posts_review(self) -> None:
        pr_payload = {"head": {"sha": "abc123"}}
        review_payload = {
            "id": 99,
            "html_url": "https://github.com/o/r/pull/1#pullrequestreview-99",
        }
        calls: list[tuple[list[str], str | None]] = []

        def fake_gh(args: list[str], *, input_text: str | None = None):
            calls.append((args, input_text))
            if args[1] == "repos/o/r/pulls/1/files":
                return (
                    0,
                    [{"filename": "src/a.py", "patch": _SAMPLE_A_PY_PATCH}],
                    "",
                )
            if args[1] == "repos/o/r/pulls/1" and "--method" not in args:
                return 0, pr_payload, ""
            if "--method" in args and "POST" in args:
                assert input_text is not None
                body = json.loads(input_text)
                self.assertEqual(body["commit_id"], "abc123")
                self.assertEqual(body["event"], "COMMENT")
                comment = body["comments"][0]
                self.assertEqual(comment["path"], "src/a.py")
                self.assertEqual(comment["line"], 3)
                self.assertEqual(comment["start_line"], 2)
                self.assertEqual(comment["start_side"], "RIGHT")
                self.assertIn("```suggestion", comment["body"])
                return 0, review_payload, ""
            return 1, None, "unexpected"

        with patch.object(pr_review_publish, "_gh_json", side_effect=fake_gh):
            result = pr_review_publish.publish_pr_review(
                pr_url="https://github.com/o/r/pull/1",
                review_text=json.dumps(
                    {
                        "summary": "ok",
                        "comments": [
                            {
                                "path": "src/a.py",
                                "start_line": 2,
                                "line": 3,
                                "body": "fix me",
                                "suggestion": "return True",
                            },
                        ],
                    }
                ),
                event="COMMENT",
                meta=pr_review_publish.ReviewPublishMeta(
                    model="composer-2.5",
                    elapsed_s=12.5,
                    usage={"total_tokens": 50, "input_tokens": 30, "output_tokens": 20},
                    agent_id="bc-test",
                    backend="sdk-cloud",
                ),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.review_id, 99)
        self.assertIn("pullrequestreview-99", result.html_url or "")
        post_calls = [
            json.loads(input_text)
            for args, input_text in calls
            if "--method" in args and input_text
        ]
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(len(post_calls[0]["comments"]), 1)
        self.assertIn("## Cursor cloud PR review", post_calls[0]["body"])
        self.assertIn("composer-2.5", post_calls[0]["body"])
        self.assertIn("12.5s", post_calls[0]["body"])
        self.assertIn("**Inline comments:** 1", post_calls[0]["body"])

    def test_publish_drops_out_of_diff_comments(self) -> None:
        pr_payload = {"head": {"sha": "abc123"}}
        review_payload = {"id": 1, "html_url": "https://github.com/o/r/pull/1#review-1"}
        pr_files_payload = [
            {"filename": "src/a.py", "patch": _SAMPLE_A_PY_PATCH},
        ]

        def fake_gh(args: list[str], *, input_text: str | None = None):
            if args[1] == "repos/o/r/pulls/1/files":
                return 0, pr_files_payload, ""
            if args[1] == "repos/o/r/pulls/1" and "--method" not in args:
                return 0, pr_payload, ""
            if "--method" in args and "POST" in args:
                body = json.loads(input_text or "{}")
                self.assertEqual(len(body.get("comments") or []), 1)
                self.assertEqual(body["comments"][0]["line"], 3)
                return 0, review_payload, ""
            return 1, None, "unexpected"

        with patch.object(pr_review_publish, "_gh_json", side_effect=fake_gh):
            result = pr_review_publish.publish_pr_review(
                pr_url="https://github.com/o/r/pull/1",
                review_text=json.dumps(
                    {
                        "summary": "mixed",
                        "comments": [
                            {"path": "src/a.py", "line": 3, "body": "keep"},
                            {"path": "src/a.py", "line": 50, "body": "drop"},
                        ],
                    }
                ),
            )
        self.assertTrue(result.ok)
        self.assertIn("dropped=1", result.message)

    def test_invalid_pr_url(self) -> None:
        result = pr_review_publish.publish_pr_review(
            pr_url="not-a-pr",
            review_text="x",
        )
        self.assertFalse(result.ok)
        self.assertIn("invalid pr_url", result.message)


class CloudDeliveryValidationTests(unittest.TestCase):
    def test_pr_review_requires_pr_url(self) -> None:
        import os

        import sdk_cloud_runner

        prev = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "crsr_test"
        try:
            result = sdk_cloud_runner.run_cloud_sdk(
                kind="review",
                prompt="review",
                repo_url="https://github.com/o/r",
                model="composer-2.5",
                prefer_fast=True,
                delivery="pr_review",
                pr_url=None,
            )
        finally:
            if prev is None:
                os.environ.pop("CURSOR_API_KEY", None)
            else:
                os.environ["CURSOR_API_KEY"] = prev
        self.assertEqual(result["status"], "error")
        self.assertIn("pr_url", result["result"])


if __name__ == "__main__":
    unittest.main()
