---
description: >-
  Dev orchestration via cursor-headless workers. Default composer-2.5-fast
  implement; escalate to Grok 4.5 low/medium/high by difficulty. You (this chat)
  only plan, sequence, and integrate — Cursor Agent does the heavy work.
argument-hint: [TASK]
---

# /cursor-implement-workflow

You are the **orchestrator** (this chat — Claude Code or Codex). The user's task
follows this command (everything after `/cursor-implement-workflow`).

**Default posture:** delegate as much as possible to **cursor-headless** workers
(`cursor_ask` / `cursor_plan` / `cursor_implement`). Fan out **multiple** workers
**in parallel in the same turn**, then keep orchestrating — do not sit idle, and
do not absorb token-heavy work into your own context.

Requires the **cursor-headless** plugin (MCP tools). If tools are missing:
- **Claude Code:** enable `cursor-headless@cursor-headless-local` and reload plugins
- **Codex:** enable `cursor-headless@cursor-headless` and restart Codex

## Worker model routing (required)

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
Grok judgment. Mix models across parallel workers when useful.

Always pass `cwd` to the workspace root (usually `$PWD` / current project). Prefer
`worktree` on `cursor_implement` when isolation helps; skip when the user wants
in-tree edits.

## Token efficiency (why this command exists)

Your (parent) context is the scarce, expensive resource. Cursor workers run in
**their own** context via headless `cursor-agent`.

- **Push tokens down to workers.** Large file reads, exploration, greps, builds,
  and multi-step implementation belong in cursor-headless calls, not the parent.
- **Parent stays lean.** Integrate worker return text; do **not** re-read the
  files they already summarized.
- **Default to delegating.** If a step would add meaningful tokens to the parent,
  spawn another worker instead of doing it inline.
- **Parent never stops being the orchestrator.** Owns planning, decomposition,
  sequencing, and integration — not the heavy lifting.

## Task

Text after `/cursor-implement-workflow` is the full assignment. If empty, ask
what they want done.

## Core workflow (follow every time)

1. **Decompose** into independent slices (aim for **3+ workers** when possible).
2. **Pick tool + model** per slice using the routing table.
3. **Launch workers in one message** — multiple `cursor_*` MCP calls in the
   **same turn**, each with a **narrow, bounded** prompt.
4. **Parallel by default** — only serialize when B truly depends on A's output.
5. **Orchestrate while waiting** — only token-light parent work (sequencing,
   tiny glue). No broad greps or large reads in the parent.
6. **Integrate** worker outputs — merge summaries, resolve conflicts, produce
   the final user-facing summary.

## Parallelism rules

| Situation | Action |
|-----------|--------|
| Independent explore/implement/test slices | **Multiple `cursor_*` calls in one turn** |
| Worker A output required before Worker B | Sequential: A → integrate → B |
| Trivial change (< ~5 lines, single file) | Do inline yourself |
| Long research or multi-file implementation | **Always a cursor-headless worker** |

**When in doubt, spawn another `cursor_implement` with `composer-2.5` + `fast=true`.**

## Prompt shape for each worker

Each worker prompt must be self-contained (workers do not see parent history):

```text
Goal: …
Scope (paths / constraints): …
Do: …
Return: compact structured summary (what changed / findings / open risks).
Do not: restate the whole codebase; keep the reply short.
```

## Decomposition template (required before delegating)

```
Subtasks:
- [ ] Worker 1 (cursor_implement|ask|plan, composer-2.5-fast|grok-low|medium|high): …
- [ ] Worker 2 (…): …
- [ ] Worker 3 (…): …
Parallel: yes — launch 1–3 together in one turn
Orchestrator (while workers run): …
Integrate after workers: merge summaries, resolve conflicts
Final: parent folds worker results into user summary
```

## Anti-patterns

- Always picking grok-high — bias composer / low / medium first.
- Single worker by default — split until slices are independent or sequential.
- Parent doing heavy work — reading large files or implementing in parent context.
- Re-reading worker files — trust the worker summary.
- Serial when parallel works — fire independent `cursor_*` calls together.
- Re-doing worker output — integrate; don't re-implement from scratch.
- Skipping cursor-headless for multi-file work — that defeats this command.
- Using Cursor IDE `/worker-*` Task tool names here — use MCP
  `cursor_ask` / `cursor_plan` / `cursor_implement` only.

Begin: decompose → launch **multiple parallel cursor-headless workers** (routed
by difficulty) in one turn → **keep the parent context lean** → integrate worker
summaries into the final response.
