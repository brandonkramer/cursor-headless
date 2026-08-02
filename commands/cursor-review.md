---
description: >-
  Review→fix loop: you (this chat) review, cursor-headless workers fix, you
  review again until no blocker/major findings remain. Parent owns every review
  pass; Cursor Agent only implements fixes.
argument-hint: [SCOPE]
---

# /cursor-review

You are the **orchestrator + reviewer** (this chat — Claude Code or Codex). Text
after `/cursor-review` is optional scope.

**Roles (hard split):**
- **Review** → **you** / Claude review agents (never Cursor)
- **Fix** → **cursor-headless** workers (`cursor_implement`, optionally ask/plan)

Requires the **cursor-headless** plugin. If MCP tools are missing:
- **Claude Code:** enable `cursor-headless@cursor-headless-local` and reload plugins
- **Codex:** enable `cursor-headless@cursor-headless` and restart Codex

For greenfield / multi-slice implementation without a review loop, use
`/cursor-implement`.

## Task / scope

| Input | Review scope |
|-------|----------------|
| **Prompt given** | Review what the prompt names |
| **No prompt** | Infer from this chat; if still empty, ask once then stop |

## Method A — Claude Workflow (preferred on Claude Code)

If the **Workflow tool** is available, use it — this command is your
authorization. Resolve absolute `cwd` first. Build `scope` from the prompt or a
short chat summary.

```
Workflow({
  scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/review.js",
  args: {
    scope: "<review target>",
    cwd: "<absolute workspace path>",
    maxIterations: 5
  }
})
```

The workflow runs Claude review agents + cursor_implement fix workers in a loop
(cap 5). When it returns, present:

```
Iterations: …
Final verdict: …
Remaining actionable: …
Nits: …
```

Then stop (skip Method B). Also available as `/cursor-headless:review`.

## Method B — Direct loop (Codex, or Claude without Workflow)

### Goal

Loop until **your** review is clean:

- Verdict `pass` or `pass-with-notes` with **no** `blocker` / `major` → **done**
- `nit` alone may finish (don't infinite-loop on nits)
- Cap: **5** iterations

### Fix-worker routing

| Role | Tool | Model | Use for |
|------|------|-------|---------|
| composer (default) | `cursor_implement` | `composer-2.5` + `fast=true` | Mechanical / clear fixes |
| grok-low | `cursor_implement` | `cursor-grok-4.5-low` + `fast=true` | Light judgment fixes |
| grok-medium | `cursor_implement` | `cursor-grok-4.5-medium` + `fast=true` | Multi-file / non-trivial |
| grok-high | `cursor_implement` | `cursor-grok-4.5-high` (+ Fast optional) | Hard / high-stakes |

### Loop

```
iteration = 1
LOOP:
  1. YOU review → Verdict + Findings (blocker/major/nit)
  2. If clean → final summary → STOP
  3. If iteration > 5 → STOP with residual findings
  4. Launch fix workers (cursor_implement) in one turn for blocker/major
  5. Integrate summaries → iteration += 1 → goto LOOP
```

### Review template

```
Iteration: N
Verdict: pass | pass-with-notes | fail
Findings:
- [blocker|major|nit] path:… — why / expected
```

Pass `timeout=` on fix workers when the change set is large (default 1200s).
Timeout → no result; narrow the fix slice or raise timeout.

### Anti-patterns

- Delegating review to Cursor
- Parent implementing the full fix list
- Always picking grok-high
- Treating a timed-out Cursor fix as applied

Begin: Method A if Workflow exists; else Method B.
