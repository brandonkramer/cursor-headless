# AGENTS.md

**cursor-headless** — thin MCP + skill over `cursor-agent --print` for **Codex**
and **Claude Code**. Parent orchestrates; Cursor workers execute. Python wrapper
+ FastMCP facade; pin `mcp>=1.9,<2`.

Current plugin version: see `.codex-plugin/plugin.json` /
`.claude-plugin/plugin.json` (keep both in sync).

## Layout

- `src/cursor_headless_mcp.py` — FastMCP tools: `cursor_ask`, `cursor_plan`, `cursor_implement`
- `skills/cursor-headless/scripts/cursor_headless.py` — CLI wrapper (what MCP shells)
- `skills/cursor-headless/SKILL.md` — routing + parent process (source of truth for behavior)
- `commands/` — `/cursor-implement`, `/cursor-review-loop`, `/cursor-loop`
- `workflows/` — Claude Code dynamic workflows (`implement.js`, `review-loop.js`)
- `.codex-plugin/`, `.claude-plugin/`, `.mcp.json` — host manifests / MCP entry
- `bin/cursor-headless-mcp[.cmd]` — optional launchers

## Parent rules (do not break)

1. Parent owns **tool + model + scope + `timeout`**. Cursor is a worker.
2. Default timeout **1200s** (`CURSOR_HEADLESS_TIMEOUT` / MCP `timeout=` / `--timeout`).
   Raise for broad maps; lower for tiny slices. Prefer path-bounded slices over one giant run.
3. **Timeout / empty / encode failure = no result.** Do not invent findings. Narrow or raise
   `timeout` and retry.
4. Model routing (no `auto`):
   - ask/plan → `cursor-grok-4.5-{low,medium,high}` (default high); opt into Fast
   - implement → `composer-2.5` default; escalate Grok by complexity; `fast=true` / `*-fast` when latency matters
   - Never pass Fable models to Cursor
5. **Windows:** prefer MCP `cursor_*` (UTF-8). Direct `cursor-agent` on a CP-1252 console can
   die on non-ASCII; wrapper/MCP force `PYTHONUTF8=1` + `safe_print`. Treat shell CP-1252
   failures as no result → retry MCP/wrapper.
6. Writes: prefer `--worktree` / MCP `worktree=`; `--force` only with `--mode default`.
   Use `require_diff=true` when success without tree changes is a failure.
7. After every write run, trust wrapper git evidence (`git status --porcelain` /
   `git diff --stat HEAD`), not Cursor narrative alone.
8. Claude Code Bash often hard-caps ~10m — launch long runs from parent MCP, not a
   Workflow worker that inherits the Bash reap.

## Commands (contributors)

```bash
# Wrapper help / smoke (needs cursor-agent on PATH for real runs)
python3 skills/cursor-headless/scripts/cursor_headless.py --help

# MCP facade (needs uv)
uv run --with 'mcp>=1.9,<2' --python 3.14 python src/cursor_headless_mcp.py --help 2>/dev/null || true

# Syntax check
python3 -m py_compile skills/cursor-headless/scripts/cursor_headless.py
uv run --with 'mcp>=1.9,<2' --python 3.14 python -m py_compile src/cursor_headless_mcp.py
```

Prove timeout/encoding changes with a fake `cursor-agent` on PATH (sleep / UTF-8
emit) — see recent session proofs. Do not claim Windows CP-1252 fixed without a
forced-cp1252 `safe_print` / e2e check.

## Edit rules

- Keep MCP defaults and wrapper defaults aligned (`DEFAULT_TIMEOUT_SEC`, model ids).
- Multiline prompts: stage via `--prompt-file` / `CURSOR_TASK-*.md` (required on Windows).
- Workflow JS: plain ASCII + LF only (CRLF / fancy Unicode breaks Claude approval UI).
- Bump **both** plugin manifests together when releasing behavior changes.
- After Codex plugin changes: reinstall/reload so
  `~/.codex/plugins/cache/` (and Claude cache) pick up the new version.
- Prefer `pnpm`/`bun` only if a JS package appears; this repo is primarily Python +
  workflow JS — never introduce `npm`/`yarn` as the package manager of record.

## Commits & PRs

- Conventional commits (`feat:`, `fix:`, `chore:`), imperative, subject ≤72 chars
- One logical change per PR; say how you verified (compile + fake-agent proofs)
- Never force-push `main`

## Docs precedence

1. `skills/cursor-headless/SKILL.md` — runtime behavior for parent agents
2. This `AGENTS.md` — contributor / orchestrator contract for the repo
3. `README.md` — install + overview for humans
