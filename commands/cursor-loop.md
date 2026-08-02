---
description: >-
  Arm Claude Code /loop (session scheduler) to repeatedly run cursor-headless
  work: implement remaining slices, review→fix, or babysit a Cursor worker.
argument-hint: "[interval] [implement|review|babysit] [SCOPE_OR_TASK]"
---

# /cursor-loop

You are in **Claude Code**. Use the session **scheduled-task / `/loop`** machinery
to re-run cursor-headless work on an interval while this session stays open.

Parse arguments after `/cursor-loop` as:

| Piece | Meaning | Default |
|-------|---------|---------|
| Leading interval (`5m`, `10m`, `1h`, …) | Fixed `/loop` schedule | omit → let `/loop` choose dynamically |
| Mode: `implement` \| `review` \| `babysit` | What each tick does | infer from the rest, else `review` |
| Remainder | Task / review scope | required (ask once if missing) |

Examples the user might type:

```text
/cursor-loop 10m review uncommitted auth changes
/cursor-loop 15m implement finish the remaining parser slices
/cursor-loop babysit keep fixing CI failures via cursor-implement
/cursor-loop 5m review
```

## Preconditions

- **cursor-headless** MCP tools available (`cursor_ask` / `cursor_plan` / `cursor_implement`)
- Dynamic workflows optional — ticks can use `/cursor-implement` or `/cursor-review`
  (Workflow when available, else direct MCP)
- Loops are **session-scoped**: they stop when the session ends (resume may restore
  unexpired tasks). Esc while waiting cancels a `/loop` wakeup.

If MCP tools are missing, tell the user to enable
`cursor-headless@cursor-headless-local` and reload plugins — do not arm a loop.

## What to schedule

Build a **tick prompt** from the mode, then arm it with `/loop` (or `CronCreate`
equivalent). Prefer invoking `/loop` so the user sees the same UX.

### Mode: `review` (default)

Each tick should:

1. Run a **scoped** `/cursor-review` on the stated scope (or inferred chat scope)
2. If verdict is clean (`pass` / `pass-with-notes`, no blocker/major): **stop the loop**
   (`ScheduleWakeup` stop / cancel the cron job) and report done in one line
3. If still dirty: summarize what was fixed this tick; wait for next interval

Tick prompt shape (pass this to `/loop`):

```text
Run /cursor-review on: <SCOPE>
cwd must be the current workspace root.
Use cursor-headless MCP for fixes only; you own the review.
If clean (no blocker/major), stop this scheduled loop and say DONE.
If still dirty, summarize fixes this tick in ≤5 bullets and end turn.
```

### Mode: `implement`

Each tick should:

1. Run `/cursor-implement` on the remaining task (or fan out `cursor_implement`
   for unfinished slices only — do not redo completed work)
2. If the task is fully done: **stop the loop** and report DONE
3. Otherwise: short progress summary; continue next tick

Tick prompt shape:

```text
Continue /cursor-implement for: <TASK>
Only unfinished slices. Prefer cursor_implement with composer-2.5 + fast;
escalate Grok only when needed. Use worktrees for parallel slices.
If the task is complete, stop this scheduled loop and say DONE.
Else summarize progress in ≤5 bullets and end turn.
```

### Mode: `babysit`

Poll external signal + fix via Cursor. Default signal: failing CI / open PR
comments on the current branch (adapt if the user named something else).

Tick prompt shape:

```text
Babysit via cursor-headless:
1) Check CI / PR review comments for the current branch (gh or local status).
2) If red or new actionable comments: /cursor-implement minimal fixes only
   (or cursor_implement MCP directly). Prefer composer-2.5-fast.
3) If green and quiet: one-line OK.
4) If user-stated stop condition is met, stop this scheduled loop and say DONE.
Do not start unrelated refactors.
```

## How to arm

1. Confirm mode, interval (or dynamic), and scope/task with the user in one short line
   if anything was inferred.
2. Run **once immediately** (first pass), then schedule the tick prompt:

```text
/loop <interval> <tick prompt>
```

If no interval was given:

```text
/loop <tick prompt>
```

(Claude chooses 1m–1h delays from activity.)

3. Tell the user: interval or dynamic, job armed, Esc cancels while waiting,
   and that this is not a durable cron (use Desktop/Routines for that).

## Anti-patterns

- Arming a loop that re-implements the whole task from scratch every tick
- Using Cursor for the **review** role in `review` mode
- Bare `/loop` with no cursor-headless instructions (defeats this command)
- Intervals under 1m (cron granularity is 1 minute)
- Leaving a loop running after DONE

Begin: parse args → first pass now → arm `/loop` with the tick prompt.
