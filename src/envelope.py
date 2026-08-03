"""Format structured CLI run results as MCP tool return envelopes."""

from __future__ import annotations

from cli_runner import CliRunResult

RunResult = CliRunResult


def format_envelope(data: RunResult) -> str:
    """Render the final MCP tool return as a structured text envelope."""
    lines: list[str] = [
        f"status: {data['status']}",
        f"backend: {data['backend']}",
        f"job_id: {data['job_id']}",
        f"model: {data['model']}",
        f"elapsed_s: {data['elapsed_s']}",
    ]

    runtime = data.get("runtime")
    if runtime:
        lines.append(f"runtime: {runtime}")

    agent_id = data.get("agent_id")
    if agent_id:
        lines.append(f"agent_id: {agent_id}")

    repo_url = data.get("repo_url")
    if repo_url:
        lines.append(f"repo_url: {repo_url}")

    pr_url = data.get("pr_url")
    if pr_url:
        lines.append(f"pr_url: {pr_url}")

    cloud_env = data.get("cloud_env")
    if cloud_env:
        env_line = f"cloud_env: {cloud_env}"
        cloud_env_name = data.get("cloud_env_name")
        if cloud_env_name:
            env_line += f" name={cloud_env_name}"
        lines.append(env_line)

    delivery = data.get("delivery")
    if delivery:
        lines.append(f"delivery: {delivery}")

    review_url = data.get("review_url")
    if review_url:
        lines.append(f"review_url: {review_url}")

    review_id = data.get("review_id")
    if review_id:
        lines.append(f"review_id: {review_id}")

    tools = data.get("tools")
    if tools is not None:
        lines.append(f"tools: {tools}")

    usage = data.get("usage")
    if isinstance(usage, dict) and usage:
        parts: list[str] = []
        for key in (
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        ):
            value = usage.get(key)
            if isinstance(value, int):
                parts.append(f"{key}={value}")
        if parts:
            lines.append(f"usage: {', '.join(parts)}")

    summary = data.get("progress_summary", "").strip()
    lines.append("progress_summary: |")
    if summary:
        lines.extend(f"  {ln}" for ln in summary.splitlines())
    else:
        lines.append("  (none)")

    result = data.get("result", "").strip()
    lines.append("result: |")
    if result:
        lines.extend(f"  {ln}" for ln in result.splitlines())
    else:
        lines.append("  (empty)")

    stderr = data.get("stderr", "").strip()
    if stderr:
        lines.append("[stderr]")
        lines.extend(stderr.splitlines())

    exit_code = data.get("exit_code", 0)
    if data["status"] == "error" and exit_code not in (0, 124):
        lines.append(f"[exit {exit_code}]")

    return "\n".join(lines)
