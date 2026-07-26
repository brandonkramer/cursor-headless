---
description: >-
  Dev orchestration via cursor-headless workers. Default composer-2.5-fast
  implement; escalate to Grok 4.5 low/medium/high by difficulty. You (this chat)
  only plan, sequence, and integrate — Cursor Agent does the heavy work.
argument-hint: [TASK]
---

# /cursor-implement

You are the **orchestrator** (this chat — Claude Code or Codex). The user's task
follows this command (everything after `/cursor-implement`).

**Default posture:** delegate as much as possible to **cursor-headless** workers
(`cursor_ask` / `cursor_plan` / `cursor_implement`). Keep the parent context lean.

Requires the **cursor-headless** plugin (MCP tools). If tools are missing:
- **Claude Code:** enable `cursor-headless@cursor-headless-local` and reload plugins
- **Codex:** enable `cursor-headless@cursor-headless` and restart Codex

## Method A — Claude Workflow (preferred on Claude Code)

If the **Workflow tool** is available in this session, use it — this command
invocation is your authorization. Resolve `cwd` to the absolute workspace path
first (usually the project root).

```
Workflow({
  scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/implement.js",
  args: {
    task: "<full assignment text after the slash command>",
    cwd: "<absolute workspace path>"
  }
})
```

Optional: pass pre-built `slices: [{goal, tool, model, worktree?, fast?}]` if you
already decomposed. Otherwise the workflow decomposes, then fans out workers.

Tell the user a short heads-up (workflow fans out multiple agents) before
launching. When it returns:

1. Integrate `workers[].summary` into the user-facing result
2. Call out `failedIndexes` / `ok: false` workers
3. Do **not** re-read whole worker diffs unless needed

Then stop (skip Method B). If Workflow is unavailable, use Method B.

You can also run the bundled workflow directly as `/cursor-headless:implement`
with the same args shape.

## Method B — Direct MCP fan-out (Codex, or Claude without Workflow)

Fan out **multiple** `cursor_*` MCP calls **in parallel in the same turn**.

### Worker model routing (required)

| Role | Tool | Model | Use for |
|------|------|-------|---------|
| composer (default) | `cursor_implement` | `composer-2.5` + `fast=true` | Explore-via-write, mechanical edits, tests, parallel fan-out |
| grok-low | `cursor_ask` / `cursor_plan` / `cursor_implement` | `cursor-grok-4.5-low` + `fast=true` | Light judgment, simple multi-step |
| grok-medium | same | `cursor-grok-4.5-medium` + `fast=true` | Non-trivial multi-file impl / refactors |
| grok-high | same | `cursor-grok-4.5-high` (+ Fast optional) | Hard bugs, design-heavy, high-stakes |

**Tool pick:**
- Read-only Q&A → `cursor_ask`
- Read-only explore / design → `cursor_plan`
- Writes / implementation → `cursor_implement` (`force` defaults true)

**Bias cheap:** prefer `composer-2.5` + `fast=true` unless the slice clearly needs
Grok judgment. Always pass `cwd`. Prefer `worktree` on implement when isolation helps.

### Core workflow

1. **Decompose** into independent slices (aim for **3+ workers** when possible).
2. **Pick tool + model** per slice.
3. **Launch workers in one message** — multiple `cursor_*` calls, narrow prompts.
4. **Parallel by default** — serialize only when B depends on A.
5. **Integrate** worker outputs — merge summaries; parent stays lean.

### Prompt shape for each worker

```text
Goal: …
Scope (paths / constraints): …
Do: …
Return: compact structured summary (what changed / findings / open risks).
Do not: restate the whole codebase; keep the reply short.
```

### Anti-patterns

- Always picking grok-high — bias composer / low / medium first.
- Parent doing heavy file reads or implementing in parent context.
- Using Cursor IDE Task tool names — use MCP `cursor_*` only.

Begin: Method A if Workflow exists; else Method B → integrate worker summaries.
