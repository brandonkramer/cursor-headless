# cursor-headless

Thin MCP tools + skill for **Codex** and **Claude Code**, backed by the
**[Cursor SDK](https://cursor.com/docs/sdk/python)** (preferred when configured)
or `cursor-agent --print` (CLI fallback). Local `cursor_ask` / `cursor_plan` /
`cursor_implement` plus cloud `cursor_cloud_*` tools for VM/pool agents.

## Tools

| Tool | Mode | Default model |
|------|------|----------------|
| `cursor_ask` | local ask (read-only) | `cursor-grok-4.5-high` (pick low\|medium\|high + Fast) |
| `cursor_plan` | local plan (read-only) | `cursor-grok-4.5-high` (pick low\|medium\|high + Fast) |
| `cursor_implement` | local default + force | `composer-2.5` (opt into Fast; escalate to Grok by complexity) |
| `cursor_cloud_plan` | cloud VM plan | `cursor-grok-4.5-high` (`repo_url` required) |
| `cursor_cloud_review` | cloud VM review | `cursor-grok-4.5-high` (`repo_url` / `pr_url`; `delivery=findings\|pr_review` — latter posts inline PR review via host `gh`) |
| `cursor_cloud_implement` | cloud VM write + optional PR | `composer-2.5` (`auto_create_pr` default true) |
| `cursor_status` | read job store | — (poll progress by `job_id`) |

Pass `model` explicitly: simple → `composer-2.5` (or `fast=true` / `*-fast` when latency matters); light → `cursor-grok-4.5-low`; medium → `…-medium`; hard → `…-high`.

Parent owns **`timeout`** (default **1200s**, env `CURSOR_HEADLESS_TIMEOUT`; cloud can use `CURSOR_HEADLESS_CLOUD_TIMEOUT`). Raise for broad maps; on timeout treat as no result — narrow or raise `timeout` and retry.

**Cloud:** needs `CURSOR_API_KEY` + GitHub `repo_url`. Use for unattended / PR work; resume via `agent_id` (`bc-…`). Local tools stay on `cwd`.

`cursor_cloud_review` with `delivery=pr_review` also needs **`gh`** authenticated on the MCP host (not the cloud VM). The posted review top comment is titled **## Cursor cloud PR review** with model/elapsed/tokens in a collapsed `<details>` block; the envelope adds `delivery`, `review_url`, `review_id`, and `usage` when reported.

**Progress:** MCP runs use `stream-json` + `notifications/progress` (when the host forwards them). Every run returns a structured envelope with `job_id` + `progress_summary`. Poll `cursor_status(job_id)` when the host allows parallel tools.

## Cursor SDK (and CLI fallback)

**SDK first:** set a Cursor user/service API key in the host environment and the
plugin auto-selects the Python SDK backend (MCP already launches with
`--with cursor-sdk`). No key → CLI (`cursor-agent`) automatically.

Same MCP surface either way (`fast`, `model`, `worktree`, `continue_session`, …).

| Order | Rule |
|-------|------|
| 1 | Per-call `backend="sdk"\|"cli"` |
| 2 | Env `CURSOR_HEADLESS_BACKEND` |
| 3 | **Auto:** SDK when an API key is present, else CLI |

| Backend | Needs | Notes |
|---------|-------|-------|
| **`sdk`** (preferred) | API key from [Dashboard → API Keys](https://cursor.com/dashboard/api) (`crsr_…` via env `CURSOR_API_KEY`) | Local agent via `cursor-sdk`; Fast via `ModelSelection` |
| `cli` | `cursor-agent` on PATH (login) | Fallback; stream-json progress |

**Fast:** MCP `fast=true` (or a `*-fast` model id) works on both. SDK maps to
`ModelSelection` params (`fast=true|false`); CLI adds `--fast`. Grok CLI ids
(`cursor-grok-4.5-{low,medium,high}`) become SDK `grok-4.5` + `effort=` + `fast=`.

**API key from a password manager:** MCP only reads the key from process env.
Inject with e.g. Proton Pass:

```bash
# cursor.env — reference only (no secret in git)
# CURSOR_API_KEY=pass://Keys/Cursor/password
pass-cli run --env-file cursor.env -- codex   # or claude
```

Bare `pass://` inside plugin JSON is not resolved — wrap the host or set a real env value.

**Windows:** live SDK works via an in-process Bridge discovery patch (upstream
`select()` / WinError 10038). Prefer MCP `cursor_*` over raw `cursor-agent` in a
CP-1252 console.

## Slash commands (Claude Code + Codex)

Parent chat orchestrates; Cursor workers execute.

| Command | What it does |
|---------|----------------|
| `/cursor-implement` | You plan/sequence/integrate; fan out parallel `cursor_ask` / `cursor_plan` / `cursor_implement` workers |
| `/cursor-review` | You review → Cursor workers fix → you review again (max 5 iterations) |
| `/cursor-loop` | Arm Claude `/loop` to re-run implement / review / CI babysit on an interval (Claude Code only) |

## Claude workflows (Claude Code only)

Requires Dynamic workflows (Claude Code ≥ 2.1.154; enable in `/config`).

| Workflow | Slash / name | What it does |
|----------|--------------|--------------|
| `workflows/implement.js` | `/cursor-headless:implement` or via `/cursor-implement` | Decompose + fan-out thin Claude wrappers that call `cursor_*` MCP |
| `workflows/review.js` | `/cursor-headless:review` or via `/cursor-review` | Claude review agents ↔ `cursor_implement` fix workers (max 5) |

Slash commands prefer the Workflow tool when available, and fall back to direct MCP fan-out (same path Codex uses).

### `/loop` recipes (Claude Code)

Session-scoped scheduler — machine + session must stay up. See [scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks).

```text
/cursor-loop 10m review uncommitted changes
/cursor-loop 15m implement finish remaining slices
/cursor-loop babysit          # CI/PR comments → cursor_implement fixes
/loop 20m /cursor-review auth PR
```

Stop with `Esc` while waiting, or ask to cancel the cron job. For durable unattended runs use Desktop scheduled tasks or Routines — not `/loop`.

## Layout

- `.codex-plugin/` — Codex plugin manifest + MCP
- `.claude-plugin/` — Claude Code marketplace + plugin manifest
- `.mcp.json` — Claude MCP server entry (`uv` → FastMCP)
- `commands/` — shared slash commands (Claude + Codex)
- `workflows/` — Claude Code dynamic workflows (`implement`, `review`)
- `skills/cursor-headless/` — shared routing skill + CLI wrapper
- `src/cursor_headless_mcp.py` — FastMCP facade
- `src/runner.py` — backend auto-select (`cli` / `sdk`)
- `src/sdk_runner.py` / `sdk_cloud_runner.py` / `sdk_bridge_patch.py` — SDK local + cloud + Windows Bridge fix
- `src/pr_review_publish.py` / `pr_diff.py` — GitHub PR review publish (`delivery=pr_review`)
- `bin/cursor-headless-mcp` — optional launcher

Requires **`uv`**. Prefer the **SDK** path (API key in env; `cursor-sdk` pulled by
MCP). CLI fallback needs **`cursor-agent`** on PATH.

MCP launch pins `mcp>=1.9,<2` and `--with cursor-sdk` (MCP Python SDK 2.x removed
`mcp.server.fastmcp`). After updating, reinstall the Codex plugin
(`codex plugin remove` / `add`) so `~/.codex/plugins/cache/` picks up the pin.

## Install (Codex)

Plugin root is the Codex marketplace root. Point Codex at this clone:

### macOS / Linux

```toml
[marketplaces.cursor-headless]
source_type = "local"
source = "$HOME/.agents/plugins/cursor-headless"

[plugins."cursor-headless@cursor-headless"]
enabled = true
```

### Windows

```toml
[marketplaces.cursor-headless]
source_type = "local"
source = '%USERPROFILE%\.agents\plugins\cursor-headless'

[plugins."cursor-headless@cursor-headless"]
enabled = true
```

Restart Codex after editing config.

## Install (Claude Code)

Same clone — marketplace root is this repo (`.claude-plugin/marketplace.json`):

```bash
claude plugin marketplace add /path/to/cursor-headless
claude plugin install cursor-headless@cursor-headless-local
```

Or in `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "cursor-headless@cursor-headless-local": true
  },
  "extraKnownMarketplaces": {
    "cursor-headless-local": {
      "source": {
        "source": "directory",
        "path": "/Users/YOU/.agents/plugins/cursor-headless"
      }
    }
  }
}
```

Restart Claude Code / `/reload-plugins` after install.

This replaces the old standalone **`cursor-implementation`** Claude plugin.

## Model routing (short)

```
ask / plan     → cursor-grok-4.5-{low,medium,high}  (+ Fast optional)
implement      → composer-2.5 (+ Fast) by default; escalate Grok by complexity
misses bar     → Codex / gpt-5.6 (orchestrator), not a heavier Cursor frontier model
```

See `skills/cursor-headless/SKILL.md` for full routing.
