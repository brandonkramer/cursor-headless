---
description: >-
  Review→fix loop: you (this chat) review, cursor-headless workers fix, you
  review again until no blocker/major findings remain. Parent owns every review
  pass; Cursor Agent only implements fixes.
argument-hint: [SCOPE]
---

# /cursor-review-loop

You are the **orchestrator + reviewer** (this chat — Claude Code or Codex). Text
after `/cursor-review-loop` is optional scope.

**Roles (hard split):**
- **Review** → **you** (the main model in this conversation). Do not delegate
  the review to Cursor.
- **Fix** → **cursor-headless** workers (`cursor_implement`, optionally
  `cursor_ask` / `cursor_plan` for bounded investigation before a fix).

Requires the **cursor-headless** plugin. If MCP tools are missing:
- **Claude Code:** enable `cursor-headless@cursor-headless-local` and reload plugins
- **Codex:** enable `cursor-headless@cursor-headless` and restart Codex

For greenfield / multi-slice implementation without a review loop, use
`/cursor-implement-workflow`.

## Task / scope

| Input | Review scope |
|-------|----------------|
| **Prompt given** | Review what the prompt names (files, PR, feature, uncommitted work, etc.) |
| **No prompt** | Review work already contextualized in **this chat** (recent changes, stated goals, files touched). Infer scope from conversation; if still empty, ask once then stop. |

Do **not** ask for a prompt when chat context already makes the review target obvious.

## Goal

Loop until **your** review is clean:

- Verdict `pass` or `pass-with-notes` with **no** `blocker` / `major` findings → **done**
- `nit` / minor notes alone may finish (report them; do not infinite-loop on nits
  unless the user asked for zero nits)
- Cap: **5** review iterations. If still dirty after 5, stop with remaining
  findings and what was fixed.

## Agents used

| Role | Who |
|------|-----|
| Review | **Parent (this chat)** — required each pass |
| Fix | `cursor_implement` (+ optional `cursor_ask` / `cursor_plan`) — same routing as `/cursor-implement-workflow` |

Do **not** fix findings inline in the parent except trivial one-liners
(< ~5 lines, single file). Do **not** ask Cursor to “review the whole thing”
instead of you.

## Fix-worker model routing (same as implement workflow)

| Role | Tool | Model | Use for |
|------|------|-------|---------|
| composer (default) | `cursor_implement` | `composer-2.5` + `fast=true` | Mechanical / clear fixes |
| grok-low | `cursor_implement` | `cursor-grok-4.5-low` + `fast=true` | Light judgment fixes |
| grok-medium | `cursor_implement` | `cursor-grok-4.5-medium` + `fast=true` | Multi-file / non-trivial fixes |
| grok-high | `cursor_implement` | `cursor-grok-4.5-high` (+ Fast optional) | Hard / high-stakes fixes |

Bias cheap: prefer composer-fast for obvious fixes; escalate only when needed.
Always pass `cwd`. Prefer `worktree` when isolation helps; skip when the user
wants in-tree edits.

## Loop (follow every time)

```
iteration = 1
LOOP:
  1. YOU review (parent chat):
     - Establish scope (prompt or short chat summary)
     - Inspect only what you need for this pass (stay targeted)
     - Produce Verdict + Findings with severity (blocker / major / nit)
     - Do not implement fixes in this step (except trivial one-liners)
  2. If clean (see Goal) → write final summary → STOP.
  3. If iteration > 5 → STOP with residual findings.
  4. Decompose blocker/major (and user-requested minor) findings into fix slices.
  5. Launch fix workers in one turn (parallel when independent):
     - narrow prompts, one finding cluster per worker when possible
     - use cursor_implement with model routing above
  6. Integrate worker summaries (parent stays lean — trust summaries; do not
     re-absorb entire worker diffs unless needed for the next review).
  7. iteration += 1 → goto LOOP (YOU review again in this chat)
```

## Review output template (each pass)

```
Iteration: N
Verdict: pass | pass-with-notes | fail
Findings:
- [blocker|major|nit] path:… — why / expected
```

## Fix-worker brief template

```
Finding: [severity] …
Location: …
Required fix: …
Out of scope: everything else
Verify: …
Return: compact summary of what changed + how verified.
```

## Token efficiency

- You own the review, but keep each pass **scoped** — do not dump the whole
  repo into context every iteration.
- Push **fix** work (and any heavy re-explore for a fix) to cursor-headless.
- Fan out multiple fix workers when findings are independent.
- After fixes, re-review the **touched areas + prior findings**, not a blind
  full-repo reread unless scope demands it.

## Final summary (required)

```
Iterations: N
Final verdict: …
Fixed: bullets
Remaining (if any): bullets
Notes: …
```

## Anti-patterns

- Delegating the review to `cursor_ask` / `cursor_plan` / a Cursor “reviewer”
  instead of reviewing in this chat.
- One giant fix worker for unrelated findings when they can parallelize.
- Looping forever on nits.
- Always picking grok-high for every fix.
- Parent implementing the full fix list instead of cursor-headless.
- Turning this into a greenfield build (use `/cursor-implement-workflow` first).

Begin: establish scope → **you review** → fix with cursor-headless workers →
**you review again** → until clean or cap.
