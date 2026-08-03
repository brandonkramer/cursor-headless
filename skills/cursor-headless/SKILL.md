---
name: cursor-headless
description: >-
  Delegate to Cursor Agent from Codex or Claude Code via thin MCP tools
  (cursor_ask/plan/implement and cursor_cloud_plan/review/implement) or
  `cursor-agent --print` headless mode. Ask/plan default to Grok 4.5 High (pick
  low|medium|high and Fast); implement defaults to Composer 2.5 (opt into Fast)
  and escalates to Grok 4.5 by complexity. Covers local + cloud VM agents,
  worktrees, cached preflight, and controlled write-capable runs. Use when the
  user asks for Cursor CLI, Cursor Agent, Cursor headless, Cursor cloud agents,
  Composer 2.5, Grok 4.5 via Cursor, or Cursor as a secondary agent.
triggers:
  - "cursor implement"
  - "cursor-agent implement"
  - "cursor headless"
  - "cursor cloud"
  - "cursor cloud review"
  - "cursor PR review"
  - "delegate implementation to cursor"
  - "composer implement"
  - "grok implement"
  - "use cursor cli"
  - "parallel cursor agents"
---

# Cursor Headless

Orchestrator (Codex or Claude Code) delegates; Cursor executes bounded headless
work via MCP. Prefer the **Cursor SDK** backend when an API key is configured;
otherwise fall back to **`cli`** (`cursor-agent --print`). Same tools and
envelope either way — SDK is often faster for short asks.

**Prefer MCP tools** — local worktree vs cloud VM are separate entrypoints:

| Tool | Use | Default model |
|------|-----|----------------|
| `cursor_ask` | Local read-only Q&A | `cursor-grok-4.5-high` — **you pick tier + Fast** |
| `cursor_plan` | Local read-only explore/plan | `cursor-grok-4.5-high` — **you pick tier + Fast** |
| `cursor_implement` | Local writes | `composer-2.5` — **you pick by complexity / when to use Fast** |
| `cursor_cloud_plan` | Cloud VM plan/explore (`repo_url`) | `cursor-grok-4.5-high` |
| `cursor_cloud_review` | Cloud VM read-only review (`repo_url` / `pr_url`) | `cursor-grok-4.5-high` |
| `cursor_cloud_implement` | Cloud VM writes + optional PR | `composer-2.5` |

CLI wrapper fallback: `scripts/cursor_headless.py` (MCP `cli` path; local only).

### Cloud agents (separate tools)

Use when work should run on a Cursor cloud VM (or self-hosted `pool` / `machine`),
survive disconnects, and optionally open a PR. Requires `CURSOR_API_KEY` +
`repo_url` (`https://github.com/org/repo`). Not a speed-up for tiny local asks.

```text
cursor_cloud_plan(prompt="…", repo_url="https://github.com/org/repo", starting_ref="main")
cursor_cloud_review(prompt="…", repo_url="…", pr_url="https://github.com/org/repo/pull/12")
# findings in envelope (default) OR GitHub PR review comments via host `gh`:
cursor_cloud_review(prompt="…", repo_url="…", pr_url="…", delivery="findings")
cursor_cloud_review(prompt="…", repo_url="…", pr_url="…", delivery="pr_review", review_event="COMMENT")
cursor_cloud_implement(prompt="…", repo_url="…", auto_create_pr=true, fast=true)
# Detach / resume (opt-in — not automatic on new PR commits):
cursor_cloud_implement(prompt="…", repo_url="…", wait=false)  # returns agent_id bc-…
cursor_cloud_implement(prompt="continue…", repo_url="…", agent_id="bc-…")
cursor_cloud_implement(prompt="continue…", repo_url="…", continue_session=true)
```

**Cloud session reuse (default = new agent each call):**

| Situation | What to do |
|-----------|------------|
| Implement follow-ups on the **same** unfinished job / PR | Resume: pass `agent_id` or `continue_session=true` |
| Fresh review after new commits | **New** agent (do not resume an old review session) |
| Plan → implement | Usually **new** implement agent; paste plan into the prompt |
| Different kind (`plan` / `review` / `implement`) | Separate stored ids — resume does not cross kinds |

Stored resume id is keyed by **`repo_url` + kind** (`cloud-plan` / `cloud-review` /
`cloud-implement`), not by PR number or branch. Pushing commits does not
auto-resume anything. Override with `agent_id` or env `CURSOR_HEADLESS_SDK_AGENT_ID`.

`delivery=pr_review` runs the cloud review, then submits a real GitHub PR review
via host `gh` auth (not the cloud-agent token). Host needs `gh` logged in with
repo access. Posts summary body plus inline comments on **Files changed** when
possible. Supports multi-line ranges (`start_line` / `start_side`), file-level
notes (`subject_type: file`), and applyable GitHub suggestion fences from a
`suggestion` field. Comments are filtered to PR diff lines before POST;
out-of-diff lines are dropped or appended to the summary.
`review_event`: `COMMENT` | `REQUEST_CHANGES` | `APPROVE`. Default
`delivery=findings` leaves results in the envelope only.

GitHub top comment shape when `delivery=pr_review` (posted via host `gh`; summary
is collapsed metadata + model findings):

```markdown
## Cursor cloud PR review

<details>
<summary><code>cursor-grok-4.5-high</code> · 28.1s · 12,345 tokens · 2 inline</summary>

- **Model:** `cursor-grok-4.5-high`
- **Elapsed:** 28.1s
- **Tokens:** 12,345 total (in 8,000 · out 4,345)
- **Event:** `COMMENT`
- **Inline comments:** 2
- **Agent:** `bc-…`
- **Job:** `…`
- **Backend:** `sdk-cloud`

</details>

---

<model summary markdown>
```

Envelope extras: `runtime: cloud`, `agent_id`, `repo_url`, optional `pr_url`,
`delivery`, `review_url`, `review_id`, `usage` (when SDK reports tokens),
`cloud_env`. Timeout env: `CURSOR_HEADLESS_CLOUD_TIMEOUT` (else same as local default).

## Cursor SDK (preferred) vs CLI

Same MCP args (`fast`, `model`, `worktree`, `continue_session`, `timeout`, …).

1. Per-call `backend="sdk"|"cli"`
2. Env `CURSOR_HEADLESS_BACKEND`
3. **Auto:** SDK when `CURSOR_API_KEY` is set, else CLI

| Backend | Needs | Notes |
|---------|-------|-------|
| **`sdk`** (preferred) | `CURSOR_API_KEY` (`crsr_…`) | MCP `uv --with cursor-sdk`; lazy-imported; Fast via `ModelSelection` |
| `cli` | `cursor-agent` login on PATH | Fallback; stream-json progress |

Windows: Bridge discovery is patched in-process so live SDK works (upstream
`selectors.select` on pipes → WinError 10038).

| Capability | SDK | CLI |
|------------|-----|-----|
| `fast=true` / `*-fast` model id | `ModelSelection` `fast=true\|false` (no `composer-2.5-fast` id) | `--fast` / `*-fast` id |
| `cursor-grok-4.5-{low,medium,high}` | `grok-4.5` + `effort=` + `fast=` | native CLI model ids |
| `worktree` | Git worktree under `<repo>/.cursor-headless/worktrees/<name>`; left on disk | `--worktree` |
| `force` | `LocalSendOptions(force=True)` (default mode) | `--force` |
| `continue_session` | Resume stored `agent_id` (`CURSOR_HEADLESS_SDK_AGENT_ID` override) | `--continue-session` / `--resume` |
| sandbox / trust / MCP approve / `--worktree-base` | — | CLI-only for now |

### API key + password managers (SDK)

MCP only reads process env `CURSOR_API_KEY` — it does not call Pass/1Password.
Mint at [Dashboard → API Keys](https://cursor.com/dashboard/api). Inject at host start:

```bash
# cursor.env contains: CURSOR_API_KEY=pass://Keys/Cursor/password
pass-cli run --env-file cursor.env -- codex   # or claude
```

Do not put bare `pass://` in plugin JSON unless the host resolves it (Codex/Claude
do not). Never print the key; never pass `--api-key` on argv.

## Following progress (parent)

1. Each `cursor_ask` / `cursor_plan` / `cursor_implement` returns an envelope with
   `job_id`, `progress_summary`, and `result`.
2. While a long call runs, hosts may surface MCP `notifications/progress` in the UI —
   that may **not** enter the model context until the tool returns.
3. If the host allows parallel tools: poll `cursor_status(job_id)` (reads
   `~/.cache/cursor-headless/jobs/<id>.json`). Long Cursor runs are offloaded from
   the MCP request thread, so status polls should not hang behind an in-flight
   `cursor_ask` / `plan` / `implement`.
4. Timeout / empty / error still means **no result** for findings — use
   `progress_summary` only as telemetry.

Never pass a Fable model to Cursor (`claude-fable-5-*`). Final high-taste review
stays on the parent (Claude in Claude Code, or Codex / gpt-5.6-sol there); use
Cursor models below for delegated work.

## Model routing (Composer vs Grok)

You choose the model — there is no `auto` heuristic.
**Defaults are non-Fast:** ask/plan → `cursor-grok-4.5-high`; implement → `composer-2.5`.
Opt into Fast with MCP `fast=true` or a `*-fast` model id when latency matters
(both backends; SDK maps to `ModelSelection` `fast=` under the hood).
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

1. Local vs cloud: tiny/local tree work → `cursor_ask` / `cursor_plan` / `cursor_implement`. Durable VM, remote-only repo, or open-a-PR jobs → `cursor_cloud_*` (`repo_url` + `CURSOR_API_KEY`).
2. Ask/plan → pick Grok low|medium|high (default high); opt into Fast when latency matters. Root-cause analysis, multi-file reasoning, and test/fix design are Grok work even when read-only. Implement → `composer-2.5` by default; opt into Fast or escalate to Grok by complexity.
3. Local modes: `--mode ask` (advisory), `--mode plan` (explore), `--mode default` (writes).
4. Prefer `--worktree` for local writes unless the user wants the current tree edited.
5. Cloud PR comments → `cursor_cloud_review(..., delivery="pr_review", pr_url=…)` (needs host `gh`). Envelope-only → `delivery="findings"`.
6. Multi-step **same** local/cloud job → `continue_session` / `agent_id`. Fresh review or new feature → new agent (see Cloud session reuse).
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

**Codex host vs tool `timeout`:** Codex can kill `tools/call` with
`tool_timeout_sec` (often 60–300s) **before** the MCP arg `timeout=` / default
1200s finishes. Error looks like `timed out awaiting tools/call after 300s`.
Fix in `~/.codex/config.toml`:

```toml
[plugins."cursor-headless@cursor-headless".mcp_servers.cursor-headless]
tool_timeout_sec = 1800
startup_timeout_sec = 120
```

Restart Codex after changing. Plugin ships `tool_timeout_sec = 1800` in
`.codex-plugin/plugin.json` (reinstall/reload to pick up).
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

Do not print API keys. CLI backend: prefer `cursor-agent login`. SDK / auto-sdk:
set `CURSOR_API_KEY` in the environment (password-manager inject OK; not `--api-key`
on the command line).

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
`/cursor-headless:review`, or slash commands that invoke them via the
Workflow tool. Scripts coordinate thin Claude agents that must call `cursor_*`
MCP — the workflow runtime cannot call MCP/shell itself.

**`/loop` scheduling** (session-scoped): use `/cursor-loop [interval] [implement|review|babysit] …`
to arm Claude’s `/loop` / cron tools so each tick re-runs `/cursor-implement` or
`/cursor-review` (or babysits CI/PR via `cursor_implement`). Loops stop when
the session ends; Esc cancels while waiting. Not a durable cloud cron.

```text
/cursor-loop 10m review uncommitted changes
/cursor-loop 15m implement remaining parser slices
/loop 20m /cursor-review auth PR   # also fine: /loop can re-invoke skills
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
