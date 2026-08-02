---
name: cursor-headless
description: >-
  Delegate to Cursor Agent from Codex or Claude Code via thin MCP tools
  (cursor_ask/plan/implement) or `cursor-agent --print` headless mode. Ask/plan
  default to Grok 4.5 High (pick low|medium|high and Fast); implement defaults to
  Composer 2.5 (opt into Fast) and escalates to Grok 4.5 by complexity. Covers
  ask/plan/write modes, worktrees, cached preflight, and controlled write-capable
  runs. Use when the user asks for Cursor CLI, Cursor Agent, Cursor headless,
  Composer 2.5, Grok 4.5 via Cursor, or Cursor as a secondary agent.
triggers:
  - "cursor implement"
  - "cursor-agent implement"
  - "cursor headless"
  - "delegate implementation to cursor"
  - "composer implement"
  - "grok implement"
  - "use cursor cli"
  - "parallel cursor agents"
---

# Cursor Headless

Orchestrator (Codex or Claude Code) delegates; Cursor Agent executes bounded
headless work via `cursor-agent --print`.

**Prefer MCP tools** (thin facade, same speed as the CLI wrapper):

| Tool | Use | Default model |
|------|-----|----------------|
| `cursor_ask` | Read-only Q&A (`--mode ask`) | `cursor-grok-4.5-high` — **you pick tier + Fast** |
| `cursor_plan` | Read-only explore/plan (`--mode plan`) | `cursor-grok-4.5-high` — **you pick tier + Fast** |
| `cursor_implement` | Writes (`--mode default`, `--force`) | `composer-2.5` — **you pick by complexity / when to use Fast** |

Fallback CLI wrapper: `scripts/cursor_headless.py` (also what the MCP server calls).

## Backend (CLI vs SDK)

Auto: **`sdk`** when `CURSOR_API_KEY` is set, else **`cli`** (`cursor-agent --print`,
MCP forces `--output-format stream-json` for live progress). Override with MCP
`backend=` or `CURSOR_HEADLESS_BACKEND`. Windows auto-stays on `cli` (upstream SDK
Bridge bug). MCP `uv` includes `cursor-sdk`. Same tools/envelope; SDK lazy-loaded.

| CLI flag | SDK backend |
|----------|-------------|
| `--worktree [name]` | Git worktree at `<repo>/.cursor-headless/worktrees/<name>`; agent `cwd` set there. Left on disk after run. |
| `--force` | `SendOptions(local=LocalSendOptions(force=True))`; write/default mode only |
| `--worktree-base`, sandbox, trust, MCP approve | CLI-only until cursor-sdk exposes equivalents |

## Following progress (parent)

1. Each `cursor_ask` / `cursor_plan` / `cursor_implement` returns an envelope with
   `job_id`, `progress_summary`, and `result`.
2. While a long call runs, hosts may surface MCP `notifications/progress` in the UI —
   that may **not** enter the model context until the tool returns.
3. If the host allows parallel tools: poll `cursor_status(job_id)` (reads
   `~/.cache/cursor-headless/jobs/<id>.json`).
4. Timeout / empty / error still means **no result** for findings — use
   `progress_summary` only as telemetry.

Never pass a Fable model to Cursor (`claude-fable-5-*`). Final high-taste review
stays on the parent (Claude in Claude Code, or Codex / gpt-5.6-sol there); use
Cursor models below for delegated work.

## Model routing (Composer vs Grok)

You choose the model — there is no `auto` heuristic.
**Defaults are non-Fast:** ask/plan → `cursor-grok-4.5-high`; implement → `composer-2.5`.
Opt into Fast with `fast=true` or a `*-fast` model id when latency matters.
Pick Grok tier (`low` / `medium` / `high`) by task complexity. Non-fast ids upgrade
when `fast` is true.

| Model id | When to use |
|----------|-------------|
| **`cursor-grok-4.5-high`** | **Default for `cursor_ask` / `cursor_plan`.** Also implement when work is hard/ambiguous/cross-cutting. |
| **`composer-2.5`** | Simple/mechanical implement (tool default). Use for ask/plan only when the question itself is mechanical; never use it for root-cause analysis, multi-file reasoning, or test/fix design. |
| **`composer-2.5-fast`** / Grok `*-fast` | Same tier when speed matters — pass `fast=true` or the `*-fast` id. |
| `cursor-grok-4.5-low` | Light ask/plan/implement — small, mostly clear. |
| `cursor-grok-4.5-medium` | Medium ask/plan/implement — multi-file reasoning, normal designs. |

### Pick Grok tier (+ Fast) by complexity

| Complexity | Model | Examples |
|------------|-------|----------|
| Simple / mechanical | `composer-2.5` (ask/plan override or implement default; `*-fast` if latency-critical) | rename, typo, lint, one-file nit, copy tweak |
| Low | `cursor-grok-4.5-low` (+ Fast optional) | short clear Q&A/plan/change, light multi-step |
| Medium | `cursor-grok-4.5-medium` (+ Fast optional) | feature slice, multi-file refactor, wire an endpoint |
| High | `cursor-grok-4.5-high` (+ Fast optional) | architecture, migration, security, ambiguous root-cause |

MCP (preferred):

```text
cursor_ask(prompt="…", cwd="$PWD")  # grok 4.5 high, timeout 1200
cursor_ask(prompt="…", cwd="$PWD", model="cursor-grok-4.5-medium", fast=true, timeout=600)
cursor_plan(prompt="…", cwd="$PWD", model="cursor-grok-4.5-low")  # light plan
cursor_plan(prompt="…", cwd="$PWD", model="composer-2.5", fast=true, timeout=1800)  # broad map
cursor_implement(prompt="…", cwd="$PWD", worktree="cursor-task")  # composer-2.5 (simple)
cursor_implement(prompt="…", cwd="$PWD", fast=true)  # composer-2.5-fast when speed matters
cursor_implement(prompt="…", cwd="$PWD", model="cursor-grok-4.5-medium-fast", timeout=1500)
```

CLI fallback (same wrapper the MCP uses):

```bash
PLUGIN_ROOT=/path/to/cursor-headless   # cloned plugin root
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" --cwd "$PWD" "…"
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" --cwd "$PWD" --model cursor-grok-4.5-medium --mode plan "…"
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" --cwd "$PWD" --model cursor-grok-4.5-high --fast "…"  # → high-fast
```

## Decision Path

1. Ask/plan → pick Grok low|medium|high (default high); opt into Fast when latency matters. Root-cause analysis, multi-file reasoning, and test/fix design are Grok work even when read-only. Implement → `composer-2.5` by default; opt into Fast or escalate to Grok by complexity.
2. `--mode ask` — one-shot advisory, no edits.
3. `--mode plan` — read-only exploration / planning.
4. `--mode default` — write-capable implementation only.
5. Prefer `--worktree` for writes unless the user wants the current tree edited.
6. Multi-step on the same task → `--continue-session` / `--resume` (faster than new sessions).
7. Skip repeated preflight after the first success (wrapper caches ~1h).
8. **You (parent) set `timeout`** per call — see process below.

## Parent orchestration process (required)

Parent (Codex / Claude Code) owns tool, model, scope, and **timeout**. Cursor is a
worker; timeout ≠ completed audit.

1. **Decompose** into narrow slices (paths + done criteria + out-of-scope).
2. **Pick tool + model + timeout** per slice before launching.
3. **Launch** via MCP (`timeout=` optional; default **1200s**) or wrapper (`--timeout`).
4. **On timeout / empty / error** → treat as **no result**. Do not invent findings.
5. **Retry** by narrowing scope **or** raising `timeout` (e.g. 1800). Prefer narrow first.
6. **Integrate** worker evidence; parent remains final reviewer.

### Timeout guidance (parent-controlled)

| Slice shape | Suggested `timeout` | Notes |
|-------------|---------------------|-------|
| Tiny Q&A / one-file | `300`–`600` | Keep default only if unsure |
| Normal plan/implement slice | `900`–`1200` | Default **1200** |
| Broad multi-app inventory / deep map | `1500`–`1800` **or split** | Prefer split into path-bounded slices |
| Claude Code Bash shelling wrapper | stay ≤ ~480 if Bash hard-caps ~10m | Prefer MCP from parent; see reap trap |

Env override for the default (MCP + CLI): `CURSOR_HEADLESS_TIMEOUT` (seconds).

```text
# Parent raises timeout for a known-broad map
cursor_plan(prompt="…", cwd="$PWD", model="composer-2.5", fast=true, timeout=1800)

# Prefer this over a single broad 30-min run
cursor_plan(prompt="Map overlaps under apps/catster-admin/server only…", cwd="$PWD", timeout=900)
cursor_plan(prompt="Map overlaps under apps/dogster-admin/server only…", cwd="$PWD", timeout=900)
```

Mechanical duplicate/file/import maps → prefer `composer-2.5` (+ `fast=true`) over
Grok High unless judgment is required.

## Performance defaults (keep it fast)

| Lever | Default | Why |
|-------|---------|-----|
| Model | ask/plan: `cursor-grok-4.5-high`; implement: `composer-2.5` | Mode-aware defaults; opt into Fast when latency matters |
| Timeout | **1200s** (`CURSOR_HEADLESS_TIMEOUT` / MCP `timeout=`) | Parent raises/lowers per slice; timeout = no result |
| Output | `text` | Avoid JSON parse/pretty cost |
| Stdin | closed (`DEVNULL`) | Prevents stdin-wait hangs |
| Preflight | cached 1h | Avoid N× `cursor-agent` cold starts |
| Pretty JSON | off | Use `--pretty-json` only for debug |
| Worktree | opt-in via flag | Creating worktrees costs time |

Preflight runs automatically on cache miss (version + status + models, ~1h TTL).
Force refresh with `--preflight`. For maximum speed on a known-good machine:
`--skip-preflight`.

Do not print API keys. Prefer login state; if needed, `CURSOR_API_KEY` in the
environment (not `--api-key` on the command line).

## Safe Headless Defaults

```bash
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" \
  --cwd "$PWD" \
  --mode ask \
  "Return exactly: CURSOR_HEADLESS_OK"
```

Equivalent raw CLI:

```bash
cursor-agent --print \
  --mode ask \
  --model cursor-grok-4.5-high \
  --output-format text \
  --sandbox enabled \
  --trust \
  --workspace "$PWD" \
  "Return exactly: CURSOR_HEADLESS_OK" </dev/null
```

Defaults unless the task needs more:

- `--model cursor-grok-4.5-high` for ask/plan; `--model composer-2.5` for implementation
- `--mode ask` or `plan` for read-only; `default` + `--force` only for approved writes
- `--output-format text` (use `json` / `stream-json` when parsing)
- `--sandbox enabled --trust --workspace "$PWD"`
- Wrapper `--timeout 1200` (parent may pass higher/lower; env `CURSOR_HEADLESS_TIMEOUT`)

Do **not** use `--force` / `--yolo` unless the user approved writes or the run is
in a disposable worktree.

## Write-Capable Delegation

Prefer an isolated worktree (git repo required):

```bash
git rev-parse --is-inside-work-tree
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" \
  --cwd "$PWD" \
  --mode default \
  --model composer-2.5 \
  --worktree cursor-task-name \
  --auto-review \
  --force \
  --output-format stream-json \
  "Implement the requested change. Do not revert unrelated changes. Return changed files and verification commands."
```

Smarter implementation when Composer is likely to struggle:

```bash
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" \
  --cwd "$PWD" \
  --mode default \
  --model cursor-grok-4.5-high \
  --worktree cursor-task-name \
  --auto-review \
  --force \
  --output-format stream-json \
  "Implement the requested change…"
```

Fast path in the **current** worktree (only if user approved and tree is OK):

```bash
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" \
  --cwd "$PWD" --mode default --force --skip-preflight \
  "…"
```

## Delegation Prompt Shape

```text
You are a delegated reviewer. Do not edit files. Use read-only inspection only.
Return:
1. Findings with file/line evidence
2. Commands you would run if given more tool access
3. Confidence and unresolved questions
```

```text
You are a delegated implementation agent. Keep changes scoped to the request.
Do not revert unrelated user changes. Prefer simple code that matches local patterns.
Return changed files, verification commands, failures, and remaining risks.
```

## MCP tools + CLI wrapper

Prefer plugin MCP tools `cursor_ask` / `cursor_plan` / `cursor_implement` (thin
facade over the script below; `--skip-preflight` by default).

CLI fallback:

```bash
python3 "$PLUGIN_ROOT/skills/cursor-headless/scripts/cursor_headless.py" \
  --cwd "$PWD" \
  --mode plan \
  "Review this change read-only and return file/line findings."
```

Useful wrapper flags:

| Flag | Purpose |
|------|---------|
| `--model` | Mode-aware default: ask/plan use Grok High; implementation uses Composer; pass `--fast` or an explicit tier when needed |
| `--fast` | Map model → `*-fast` variant when applicable |
| `--skip-preflight` / `--preflight` | Skip or force auth/model checks |
| `--prompt-file` | Load prompt text (wrapper still stages `CURSOR_TASK-*` for cursor-agent) |
| `--require-diff` | Fail write runs with clean `git status --porcelain` |
| `--inline-prompt` | Force argv delivery (one-line smoke only; unsafe on Windows) |
| `--timeout` | Default **1200s** (parent-adjustable; env `CURSOR_HEADLESS_TIMEOUT`; watch Bash 10m reap) |
| `--raw` / `--pretty-json` | Output control |
| `--continue-session` / `--resume` | Faster multi-step |
| `--approve-mcps` | Headless MCP approval |
| `--force` | Only with `--mode default` after write approval |

## Stream Parsing

For `stream-json`, treat the final line with `type == "result"` as authoritative.
The wrapper streams lines to stdout and does not summarize them.

For `json`, default is compact (not pretty). Use `--pretty-json` only when debugging.

## Claude Code notes

On Claude Code this plugin loads the same skill + MCP server + `workflows/`.
Prefer MCP tools when available; otherwise use the CLI wrapper / `cursor-agent -p`
with the same model routing.

**Workflows** (Dynamic workflows enabled): `/cursor-headless:implement` and
`/cursor-headless:review-loop`, or slash commands that invoke them via the
Workflow tool. Scripts coordinate thin Claude agents that must call `cursor_*`
MCP — the workflow runtime cannot call MCP/shell itself.

**`/loop` scheduling** (session-scoped): use `/cursor-loop [interval] [implement|review|babysit] …`
to arm Claude’s `/loop` / cron tools so each tick re-runs `/cursor-implement` or
`/cursor-review-loop` (or babysits CI/PR via `cursor_implement`). Loops stop when
the session ends; Esc cancels while waiting. Not a durable cloud cron.

```text
/cursor-loop 10m review uncommitted changes
/cursor-loop 15m implement remaining parser slices
/loop 20m /cursor-review-loop auth PR   # also fine: /loop can re-invoke skills
```

Label thin wrapper agents `composer:…` or `grok:…`. Escalate heavier reasoning
to **`codex-implementation`** (codex-headless Claude plugin), not a frontier
model on the Cursor CLI.

Replaces the former standalone **`cursor-implementation`** Claude plugin.

## Prompt delivery (Windows / multiline)

Never rely on multiline argv into `cursor-agent` — the Windows `.cmd` shim truncates
to the first line. The wrapper always stages a unique `CURSOR_TASK-<hash>-….md` in
`--cwd` on Windows (and for any multiline / long prompt elsewhere), then sends a
one-line bootstrap. Unique names unlock parallel slices; do **not** share a single
`CURSOR_TASK.md` across workers.

MCP tools write a temp `--prompt-file` first (same reason). Prefer MCP or the
wrapper; do not paste multiline prompts straight onto `cursor-agent`.

Subprocess decode uses `encoding=utf-8, errors=replace`. Wrapper/MCP also force
`PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` and `safe_print` so Windows **CP-1252**
consoles do not crash on em-dashes / fancy quotes from Cursor output.

**Windows:** prefer MCP `cursor_*` (stdio UTF-8) over shelling `cursor-agent` into a
legacy console. If a direct/shell path hits `charmap` / CP-1252 encode errors, retry
via MCP or `cursor_headless.py` (UTF-8 path) — treat the failed shell path as no result.

After every run the wrapper appends `git status --porcelain` + `git diff --stat HEAD`.
Pass `--require-diff` / MCP `require_diff=true` on write runs to fail clean-tree "success".

## Bash 10-minute / workflow-reap trap

Claude Code Bash tools often hard-cap around **10 minutes**. A Workflow / thin
subagent that shells `cursor_headless.py` (or MCP that waits on it) gets
**backgrounded then reaped** with **zero tree changes** if the run exceeds that
cap — while Cursor may still be running elsewhere or may have fabricated a completion
report.

Wrapper default is **1200s**; that only helps when the **host** waits that long
(Codex MCP / parent MCP). Claude Bash shelling can still reap earlier.

**Do this instead:**

1. Launch long Cursor runs from the **parent session** via MCP (pass `timeout=`),
   not from a Workflow worker that inherits a ~10m Bash cap.
2. Never trust Cursor's narrative alone — check wrapper git evidence / `git status`.
3. Prefer path-bounded slices under ~8–15 minutes wall, or use `--worktree` + unique
   task files and poll from the parent.
4. On timeout: **no result** → narrow scope or raise `timeout` and retry once.

Workflow scripts in this plugin are plain ASCII + LF only (CRLF / fancy Unicode
breaks Claude's approval dialog: "control characters that would be hidden").

## Reporting

When returning Cursor results to the user, include:

- Invocation path (ask / plan / write, worktree, resume/continue)
- Model used (`composer-2.5` / `composer-2.5-fast` vs `cursor-grok-4.5-{low,medium,high}`[+`-fast`])
- Cursor Agent version (from cache or `--preflight`)
- Read-only vs auto-review vs force
- Workspace / worktree boundary
- Result, denials, failures, incomplete output
- Wrapper git evidence (porcelain / diffstat); treat clean tree + "done" as failure unless expected
