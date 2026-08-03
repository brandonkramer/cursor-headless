"""Publish structured review findings as a GitHub pull-request review.

Uses host ``gh`` auth (not the cloud-agent token). Supports GitHub's review API:
summary body + optional inline comments, submitted as COMMENT / REQUEST_CHANGES /
APPROVE.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

from pr_diff import FileDiffMap, map_comment_to_payload, parse_patch

ReviewEvent = Literal["COMMENT", "REQUEST_CHANGES", "APPROVE"]
Delivery = Literal["findings", "pr_review"]

_PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class PrRef:
    owner: str
    repo: str
    number: int
    url: str


SubjectType = Literal["line", "file"]


@dataclass(frozen=True)
class InlineComment:
    path: str
    line: int
    body: str
    side: str = "RIGHT"
    start_line: int | None = None
    start_side: str | None = None
    subject_type: SubjectType = "line"
    suggestion: str | None = None


@dataclass(frozen=True)
class ParsedReview:
    summary: str
    event: ReviewEvent
    comments: list[InlineComment]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ReviewPublishMeta:
    """Host-side metadata prepended to the GitHub PR review summary body."""

    model: str | None = None
    elapsed_s: float | None = None
    usage: dict[str, int] | None = None
    agent_id: str | None = None
    tools: int | None = None
    cloud_env: str | None = None
    backend: str | None = None
    job_id: str | None = None
    event: ReviewEvent | None = None
    inline_count: int | None = None


def format_token_usage(usage: dict[str, int] | None) -> str | None:
    if not usage:
        return None
    parts: list[str] = []
    if "input_tokens" in usage:
        parts.append(f"in {usage['input_tokens']:,}")
    if "output_tokens" in usage:
        parts.append(f"out {usage['output_tokens']:,}")
    if usage.get("cache_read_tokens"):
        parts.append(f"cache read {usage['cache_read_tokens']:,}")
    if usage.get("cache_write_tokens"):
        parts.append(f"cache write {usage['cache_write_tokens']:,}")
    if usage.get("reasoning_tokens"):
        parts.append(f"reasoning {usage['reasoning_tokens']:,}")
    total = usage.get("total_tokens")
    if total is not None:
        base = f"{total:,} total"
        return f"{base} ({' · '.join(parts)})" if parts else base
    return " · ".join(parts) if parts else None


def _meta_summary_label(meta: ReviewPublishMeta) -> str:
    """One-line <summary> teaser for the collapsed metadata block."""
    bits: list[str] = []
    if meta.model:
        bits.append(f"<code>{meta.model}</code>")
    if meta.elapsed_s is not None:
        bits.append(f"{meta.elapsed_s:g}s")
    tokens = format_token_usage(meta.usage)
    if tokens:
        # Keep the summary line short: total only when available.
        total = meta.usage.get("total_tokens") if meta.usage else None
        bits.append(f"{total:,} tokens" if isinstance(total, int) else tokens)
    elif meta.usage is None:
        bits.append("tokens n/a")
    if meta.inline_count is not None:
        bits.append(f"{meta.inline_count} inline")
    return " · ".join(bits) if bits else "run metadata"


def format_pr_review_body(
    summary: str,
    meta: ReviewPublishMeta | None,
) -> str:
    """Title + collapsed run metadata, then the model summary (GitHub review body)."""
    body = (summary or "").strip() or "_No summary._"
    if meta is None:
        return body

    meta_lines: list[str] = []
    if meta.model:
        meta_lines.append(f"- **Model:** `{meta.model}`")
    if meta.elapsed_s is not None:
        meta_lines.append(f"- **Elapsed:** {meta.elapsed_s:g}s")
    tokens = format_token_usage(meta.usage)
    if tokens:
        meta_lines.append(f"- **Tokens:** {tokens}")
    elif meta.usage is None:
        meta_lines.append("- **Tokens:** _(not reported)_")
    if meta.event:
        meta_lines.append(f"- **Event:** `{meta.event}`")
    if meta.inline_count is not None:
        meta_lines.append(f"- **Inline comments:** {meta.inline_count}")
    if meta.agent_id:
        meta_lines.append(f"- **Agent:** `{meta.agent_id}`")
    if meta.job_id:
        meta_lines.append(f"- **Job:** `{meta.job_id}`")
    if meta.tools is not None:
        meta_lines.append(f"- **Tools:** {meta.tools}")
    if meta.cloud_env:
        meta_lines.append(f"- **Cloud env:** `{meta.cloud_env}`")
    if meta.backend:
        meta_lines.append(f"- **Backend:** `{meta.backend}`")

    # GitHub collapsed section: blank lines around <summary> content matter.
    # https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/organizing-information-with-collapsed-sections
    lines = [
        "## Cursor cloud PR review",
        "",
        "<details>",
        f"<summary>{_meta_summary_label(meta)}</summary>",
        "",
        *meta_lines,
        "",
        "</details>",
        "",
        "---",
        "",
        body,
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    message: str
    review_id: int | None = None
    review_url: str | None = None
    html_url: str | None = None


def parse_pr_url(pr_url: str) -> PrRef | None:
    text = pr_url.strip()
    match = _PR_URL_RE.fullmatch(text)
    if not match:
        return None
    return PrRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(match.group("number")),
        url=text.split("?")[0].rstrip("/"),
    )


def _normalize_event(raw: object, default: ReviewEvent) -> ReviewEvent:
    if not isinstance(raw, str):
        return default
    value = raw.strip().upper().replace("-", "_")
    if value in {"COMMENT", "REQUEST_CHANGES", "APPROVE"}:
        return value  # type: ignore[return-value]
    if value in {"REQUEST CHANGES", "CHANGES_REQUESTED"}:
        return "REQUEST_CHANGES"
    return default


def extract_review_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object extraction from model output."""
    blob = text.strip()
    if not blob:
        return None

    candidates: list[str] = []
    fence = _JSON_FENCE_RE.search(blob)
    if fence:
        candidates.append(fence.group(1))
    if blob.startswith("{"):
        candidates.append(blob)
    # last {...} block
    start = blob.find("{")
    end = blob.rfind("}")
    if start >= 0 and end > start:
        candidates.append(blob[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _normalize_side(raw: object, *, default: str = "RIGHT") -> str:
    side = str(raw or default).strip().upper()
    if side in {"LEFT", "RIGHT"}:
        return side
    return default


def _normalize_subject_type(raw: object) -> SubjectType:
    if not isinstance(raw, str):
        return "line"
    value = raw.strip().lower()
    if value == "file":
        return "file"
    return "line"


def format_comment_body_for_github(body: str, suggestion: str | None) -> str:
    """Append a GitHub suggestion fence when replacement text is provided."""
    if not suggestion:
        return body
    replacement = suggestion.rstrip("\n")
    return f"{body}\n\n```suggestion\n{replacement}\n```"


def inline_comment_to_github_dict(comment: InlineComment) -> dict[str, Any]:
    """Map a parsed inline comment to GitHub create-review comment payload."""
    entry: dict[str, Any] = {
        "path": comment.path,
        "body": format_comment_body_for_github(comment.body, comment.suggestion),
    }
    if comment.subject_type == "file":
        entry["subject_type"] = "file"
        return entry

    entry["line"] = comment.line
    entry["side"] = comment.side
    if comment.start_line is not None:
        entry["start_line"] = comment.start_line
        entry["start_side"] = comment.start_side or comment.side
    return entry


def parse_review_payload(
    text: str,
    *,
    default_event: ReviewEvent = "COMMENT",
) -> ParsedReview:
    data = extract_review_json(text)
    if data is None:
        summary = text.strip() or "(no review summary)"
        return ParsedReview(summary=summary, event=default_event, comments=[], raw={})

    summary_raw = data.get("summary") or data.get("body") or ""
    summary = str(summary_raw).strip() if summary_raw else text.strip()
    event = _normalize_event(data.get("event"), default_event)

    comments: list[InlineComment] = []
    raw_comments = data.get("comments") or data.get("inline_comments") or []
    if isinstance(raw_comments, list):
        for item in raw_comments:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            body = str(item.get("body") or "").strip()
            if not path or not body:
                continue

            subject_type = _normalize_subject_type(item.get("subject_type"))
            line = 0
            if subject_type == "line":
                try:
                    line = int(item.get("line"))
                except (TypeError, ValueError):
                    continue
                if line <= 0:
                    continue

            side = _normalize_side(item.get("side"))
            start_line: int | None = None
            start_line_raw = item.get("start_line")
            if start_line_raw is not None:
                try:
                    start_line = int(start_line_raw)
                except (TypeError, ValueError):
                    start_line = None
                if start_line is not None and start_line <= 0:
                    start_line = None

            start_side_raw = item.get("start_side")
            start_side = (
                _normalize_side(start_side_raw, default=side)
                if start_side_raw is not None
                else None
            )

            suggestion_raw = item.get("suggestion")
            suggestion = (
                str(suggestion_raw)
                if suggestion_raw is not None and str(suggestion_raw).strip()
                else None
            )

            severity = str(item.get("severity") or "").strip()
            if severity:
                body = f"**{severity}:** {body}"

            comments.append(
                InlineComment(
                    path=path,
                    line=line,
                    body=body,
                    side=side,
                    start_line=start_line,
                    start_side=start_side,
                    subject_type=subject_type,
                    suggestion=suggestion,
                )
            )

    if not summary:
        summary = f"Automated review ({len(comments)} inline comment(s))."
    return ParsedReview(summary=summary, event=event, comments=comments, raw=data)


def _gh_json(args: list[str], *, input_text: str | None = None) -> tuple[int, Any, str]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
    except FileNotFoundError:
        return 127, None, "gh CLI not found on PATH (required to post PR reviews)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, None, str(exc)

    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return proc.returncode, None, stderr or stdout or f"gh exited {proc.returncode}"
    if not stdout:
        return 0, None, ""
    try:
        return 0, json.loads(stdout), ""
    except json.JSONDecodeError:
        return 0, stdout, ""


def fetch_pr_file_patches(pr: PrRef) -> tuple[dict[str, FileDiffMap], str]:
    """Fetch PR changed files and parse commentable diff lines per path."""
    code, data, err = _gh_json(
        [
            "api",
            f"repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/files",
            "--paginate",
        ]
    )
    if code != 0:
        return {}, err or "failed to fetch PR files"
    if not isinstance(data, list):
        return {}, "unexpected PR files response"

    maps: dict[str, FileDiffMap] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        path = item.get("filename")
        if not isinstance(path, str) or not path.strip():
            continue
        patch = item.get("patch")
        if isinstance(patch, str) and patch.strip():
            maps[path.strip()] = parse_patch(patch)
        else:
            maps[path.strip()] = FileDiffMap(
                left_lines=frozenset(),
                right_lines=frozenset(),
                left_hunk=frozenset(),
                right_hunk=frozenset(),
            )
    return maps, ""


def filter_comments_to_diff(
    pr: PrRef,
    comments: list[InlineComment],
    *,
    file_maps: dict[str, FileDiffMap] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map comments onto the PR diff; drop any that are not commentable."""
    maps = file_maps
    if maps is None:
        maps, err = fetch_pr_file_patches(pr)
        if err:
            return [], [f"failed to fetch PR files: {err}"]

    valid: list[dict[str, Any]] = []
    dropped: list[str] = []
    for comment in comments:
        payload, reason = map_comment_to_payload(comment, maps)
        if payload is None:
            if reason:
                dropped.append(reason)
            continue
        # Diff mapper validates lines; attach suggestion fence on the body.
        payload["body"] = format_comment_body_for_github(
            comment.body, comment.suggestion
        )
        valid.append(payload)
    return valid, dropped


def _summary_with_dropped_inlines(
    summary: str,
    comments: list[InlineComment],
    dropped: list[str],
) -> str:
    lines = [summary, "", "<!-- inline comments not on diff -->"]
    if dropped:
        lines.append("Dropped inline comment(s):")
        lines.extend(f"- {reason}" for reason in dropped)
    lines.extend(["", "<!-- appended as text -->"])
    for comment in comments:
        body = format_comment_body_for_github(comment.body, comment.suggestion)
        if comment.subject_type == "file":
            lines.append(f"- `{comment.path}`: {body}")
        else:
            lines.append(f"- `{comment.path}:{comment.line}`: {body}")
    return "\n".join(lines)


def fetch_pr_head_sha(pr: PrRef) -> tuple[str | None, str]:
    code, data, err = _gh_json(
        [
            "api",
            f"repos/{pr.owner}/{pr.repo}/pulls/{pr.number}",
        ]
    )
    if code != 0:
        return None, err or "failed to fetch PR head sha"
    if isinstance(data, dict):
        head = data.get("head")
        if isinstance(head, dict):
            sha = head.get("sha")
            if isinstance(sha, str) and sha.strip():
                return sha.strip(), ""
    return None, "empty PR head sha"


def publish_pr_review(
    *,
    pr_url: str,
    review_text: str,
    event: ReviewEvent = "COMMENT",
    dry_run: bool = False,
    meta: ReviewPublishMeta | None = None,
) -> PublishResult:
    """Create a GitHub PR review from model review text / structured JSON."""
    pr = parse_pr_url(pr_url)
    if pr is None:
        return PublishResult(
            ok=False,
            message=f"error: invalid pr_url for GitHub review: {pr_url!r}",
        )

    parsed = parse_review_payload(review_text, default_event=event)
    # MCP/caller review_event wins; model JSON may still suggest event in payload.
    final_event = event

    commit_id, sha_err = fetch_pr_head_sha(pr)
    if not commit_id:
        return PublishResult(ok=False, message=f"error: {sha_err}")

    valid_comments, dropped = filter_comments_to_diff(pr, parsed.comments)

    publish_meta = meta
    if meta is not None:
        publish_meta = ReviewPublishMeta(
            model=meta.model,
            elapsed_s=meta.elapsed_s,
            usage=meta.usage,
            agent_id=meta.agent_id,
            tools=meta.tools,
            cloud_env=meta.cloud_env,
            backend=meta.backend,
            job_id=meta.job_id,
            event=final_event,
            inline_count=len(valid_comments),
        )

    summary_body = format_pr_review_body(parsed.summary, publish_meta)

    payload: dict[str, Any] = {
        "commit_id": commit_id,
        "body": summary_body,
        "event": final_event,
    }
    if valid_comments:
        payload["comments"] = valid_comments
    elif parsed.comments:
        payload["body"] = _summary_with_dropped_inlines(
            summary_body, parsed.comments, dropped
        )

    dropped_note = ""
    if dropped:
        dropped_note = f" dropped={len(dropped)}"

    if dry_run:
        return PublishResult(
            ok=True,
            message=(
                "dry_run: would submit PR review "
                f"event={final_event} inline={len(valid_comments)}"
                f"{dropped_note} on {pr.url}"
            ),
        )

    code, data, err = _gh_json(
        [
            "api",
            "--method",
            "POST",
            f"repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews",
            "--input",
            "-",
        ],
        input_text=json.dumps(payload),
    )
    if code != 0:
        # Retry summary-only if inline comments rejected (line not in diff, etc.)
        if parsed.comments:
            fallback = {
                "commit_id": commit_id,
                "body": _summary_with_dropped_inlines(
                    summary_body
                    + "\n\n<!-- inline comments failed on POST; appended as text -->",
                    parsed.comments,
                    dropped or [err],
                ),
                "event": final_event,
            }
            code2, data2, err2 = _gh_json(
                [
                    "api",
                    "--method",
                    "POST",
                    f"repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews",
                    "--input",
                    "-",
                ],
                input_text=json.dumps(fallback),
            )
            if code2 == 0 and isinstance(data2, dict):
                review_id = data2.get("id")
                html = data2.get("html_url")
                return PublishResult(
                    ok=True,
                    message=(
                        f"posted PR review (summary-only fallback after inline failure: {err})"
                        f"{dropped_note}; event={final_event}"
                    ),
                    review_id=int(review_id) if isinstance(review_id, int) else None,
                    html_url=str(html) if isinstance(html, str) else None,
                    review_url=str(html) if isinstance(html, str) else None,
                )
            return PublishResult(
                ok=False,
                message=f"error: gh PR review failed: {err}; fallback also failed: {err2}",
            )
        return PublishResult(ok=False, message=f"error: gh PR review failed: {err}")

    if not isinstance(data, dict):
        return PublishResult(ok=True, message=f"posted PR review event={final_event}")

    review_id = data.get("id")
    html = data.get("html_url")
    return PublishResult(
        ok=True,
        message=(
            f"posted PR review event={final_event} "
            f"inline={len(valid_comments)}{dropped_note}"
        ),
        review_id=int(review_id) if isinstance(review_id, int) else None,
        html_url=str(html) if isinstance(html, str) else None,
        review_url=str(html) if isinstance(html, str) else None,
    )


def review_json_instructions(*, event: ReviewEvent = "COMMENT") -> str:
    """Prompt appendix so the cloud agent emits structured review JSON."""
    return f"""
Return ONLY a single JSON object (no markdown fences, no prose outside JSON) with this shape:
{{
  "summary": "short overall PR review body (markdown ok inside the string)",
  "event": "{event}",
  "comments": [
    {{
      "path": "relative/path.ext",
      "line": 12,
      "side": "RIGHT",
      "start_line": 10,
      "start_side": "RIGHT",
      "subject_type": "line",
      "body": "inline finding with evidence",
      "severity": "nit",
      "suggestion": "exact replacement line(s) without markdown fences"
    }}
  ]
}}
Rules:
- Return ONLY JSON. No prose, headings, or markdown fences outside the JSON object.
- Prefer comments only on changed lines in the PR diff (RIGHT side) when possible.
- Use start_line and start_side for multi-line ranges (start_line <= line).
- Use subject_type \"file\" with path + body only for file-level notes (omit line).
- Use suggestion when proposing an exact replacement; put replacement text in suggestion, not in body.
- Use an empty comments array when only a top-level summary is warranted.
- event must be one of COMMENT, REQUEST_CHANGES, APPROVE (default {event}).
- path must be repository-relative. Do not edit files.
""".strip()
