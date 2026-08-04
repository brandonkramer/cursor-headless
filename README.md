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

## Cursor SDK (and CLI fallback)

**SDK first** when the MCP process has `CURSOR_API_KEY`; otherwise **CLI**
(`cursor-agent`) automatically. MCP already launches with `--with cursor-sdk`.

Same MCP surface either way (`fast`, `model`, `worktree`, `continue_session`, …).

| Order | Rule |
|-------|------|
| 1 | Per-call `backend="sdk"\|"cli"` |
| 2 | Env `CURSOR_HEADLESS_BACKEND` |
| 3 | **Auto:** SDK when `CURSOR_API_KEY` is set **in the MCP process**, else CLI |

| Backend | Needs | Notes |
|---------|-------|-------|
| **`sdk`** (preferred) | `CURSOR_API_KEY` (`crsr_…`) in the MCP process env | Local agent via `cursor-sdk`; Fast via `ModelSelection` |
| `cli` | `cursor-agent` on PATH (login) | Fallback; stream-json progress |

**Fast:** MCP `fast=true` (or a `*-fast` model id) works on both. SDK maps to
`ModelSelection` params (`fast=true|false`); CLI adds `--fast`. Grok CLI ids
(`cursor-grok-4.5-{low,medium,high}`) become SDK `grok-4.5` + `effort=` + `fast=`.

### Configure `CURSOR_API_KEY` (Codex desktop)

The plugin reads **only** process env `CURSOR_API_KEY`. It does not resolve
Pass/1Password, and it does not hot-reload keys after MCP start.

Mint a key at [Dashboard → API Keys](https://cursor.com/dashboard/api), then:

1. Put it in the Codex desktop env file (secret stays out of `config.toml`):

```bash
# macOS / Linux
printf 'CURSOR_API_KEY=crsr_…\n' > ~/.codex/.env
chmod 600 ~/.codex/.env

# Windows (PowerShell) — do not echo the key into chat/logs
@"
CURSOR_API_KEY=crsr_…
"@ | Set-Content -Path "$env:USERPROFILE\.codex\.env" -Encoding utf8
```

2. Whitelist forwarding into this plugin’s MCP process. Plugin **≥ 0.3.12**
   ships `env_vars: ["CURSOR_API_KEY"]` in the Codex manifest. For older
   installs (or to force it in user config):

```toml
[plugins."cursor-headless@cursor-headless".mcp_servers.cursor-headless]
env_vars = ["CURSOR_API_KEY"]
```

Codex: `env` = literal values on the server; `env_vars` = names forwarded from
the parent / `.env`. Without the whitelist, `.env` can be valid and MCP still
sees an empty `CURSOR_API_KEY`.

3. **Fully quit and reopen** the Codex desktop app. A new chat is not enough.

| Do | Don’t |
|----|--------|
| `CURSOR_API_KEY=crsr_…` in `~/.codex/.env` | Put the secret in `mcp_servers.*.env` literals |
| `env_vars = ["CURSOR_API_KEY"]` (plugin ≥ 0.3.12) | Assume `.env` alone reaches MCP without forwarding |
| Restart Codex after `.env` / plugin updates | Expect a live MCP server to pick up a key mid-session |
| `backend="cli"` + `cursor-agent login` if you only need CLI | Commit `.env` or paste keys into chat |

**CLI-launched Codex / password managers:**

```bash
# cursor.env — reference only (no secret in git)
# CURSOR_API_KEY=pass://Keys/Cursor/password
pass-cli run --env-file cursor.env -- codex   # or claude
```

Bare `pass://` inside plugin JSON is not resolved — wrap the host or use a real
value in `~/.codex/.env`.

**Windows:** live SDK works via an in-process Bridge discovery patch (upstream
`select()` / WinError 10038). Prefer MCP `cursor_*` over raw `cursor-agent` in a
CP-1252 console. If SDK reports a missing key but `.env` looks correct, restart
Codex before debugging the Bridge.

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

## Operating notes

Parent owns the run; Cursor is a worker. Miss these and you get false failures or silent “no result.”

| Topic | Rule |
|-------|------|
| **Timeout** | Parent owns `timeout` (default **1200s**, env `CURSOR_HEADLESS_TIMEOUT`; cloud can use `CURSOR_HEADLESS_CLOUD_TIMEOUT`). Raise for broad maps. On timeout treat as **no result** — narrow the slice or raise `timeout` and retry; do not invent findings. |
| **Cloud** | Needs `CURSOR_API_KEY` + GitHub `repo_url`. Use for unattended / PR work; resume via `agent_id` (`bc-…`). Local tools stay on `cwd`. |
| **PR review delivery** | `cursor_cloud_review` with `delivery=pr_review` also needs **`gh`** authenticated on the **MCP host** (not the cloud VM). Posted top comment is titled **## Cursor cloud PR review** with model/elapsed/tokens in a collapsed `<details>` block; envelope may add `delivery`, `review_url`, `review_id`, and `usage`. |
| **Progress** | MCP uses `stream-json` + `notifications/progress` (when the host forwards them). Every run returns an envelope with `job_id` + `progress_summary`. Long `cursor_*` tools run off the MCP request thread (`asyncio.to_thread`), so parallel `cursor_status(job_id)` polls can answer while a job is in flight. |
| **SDK key** | Codex desktop: `CURSOR_API_KEY` in `~/.codex/.env` **and** `env_vars = ["CURSOR_API_KEY"]` (plugin ≥ 0.3.12), then fully restart. `.env` alone is not enough if forwarding is missing (see [Configure `CURSOR_API_KEY`](#configure-cursor_api_key-codex-desktop)). |
