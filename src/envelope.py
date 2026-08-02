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

    tools = data.get("tools")
    if tools is not None:
        lines.append(f"tools: {tools}")

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
