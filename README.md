# cursor-headless

Thin MCP tools + skill over `cursor-agent --print` for **Codex** and **Claude Code**.

## Tools

| Tool | Mode | Default model |
|------|------|----------------|
| `cursor_ask` | ask (read-only) | `cursor-grok-4.5-high` (pick low\|medium\|high + Fast) |
| `cursor_plan` | plan (read-only) | `cursor-grok-4.5-high` (pick low\|medium\|high + Fast) |
| `cursor_implement` | default + force | `composer-2.5` (opt into Fast; escalate to Grok 4.5 low/medium/high by complexity) |

Pass `model` explicitly: simple → `composer-2.5` (or `*-fast` / `fast=true` when latency matters); light → `cursor-grok-4.5-low`; medium → `…-medium`; hard → `…-high`.

## Slash commands (Claude Code + Codex)

Parent chat orchestrates; Cursor workers execute.

| Command | What it does |
|---------|----------------|
| `/cursor-implement` | You plan/sequence/integrate; fan out parallel `cursor_ask` / `cursor_plan` / `cursor_implement` workers |
| `/cursor-review-loop` | You review → Cursor workers fix → you review again (max 5 iterations) |

## Claude workflows (Claude Code only)

Requires Dynamic workflows (Claude Code ≥ 2.1.154; enable in `/config`).

| Workflow | Slash / name | What it does |
|----------|--------------|--------------|
| `workflows/implement.js` | `/cursor-headless:implement` or via `/cursor-implement` | Decompose + fan-out thin Claude wrappers that call `cursor_*` MCP |
| `workflows/review-loop.js` | `/cursor-headless:review-loop` or via `/cursor-review-loop` | Claude review agents ↔ `cursor_implement` fix workers (max 5) |

Slash commands prefer the Workflow tool when available, and fall back to direct MCP fan-out (same path Codex uses).

## Layout

- `.codex-plugin/` — Codex plugin manifest + MCP
- `.claude-plugin/` — Claude Code marketplace + plugin manifest
- `.mcp.json` — Claude MCP server entry (`uv` → FastMCP)
- `commands/` — shared slash commands (Claude + Codex)
- `workflows/` — Claude Code dynamic workflows (`implement`, `review-loop`)
- `skills/cursor-headless/` — shared routing skill + CLI wrapper
- `src/cursor_headless_mcp.py` — FastMCP facade
- `bin/cursor-headless-mcp` — optional launcher

Requires `uv` and `cursor-agent` on PATH.

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
