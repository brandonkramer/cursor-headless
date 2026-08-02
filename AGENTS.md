# AGENTS.md

**cursor-headless** — thin MCP + skill over `cursor-agent --print` for **Codex**
and **Claude Code**. Parent orchestrates; Cursor workers execute. Python wrapper
+ FastMCP facade; pin `mcp>=1.9,<2`.

Current plugin version: see `.codex-plugin/plugin.json` /
`.claude-plugin/plugin.json` (keep both in sync).

## Layout

- `src/cursor_headless_mcp.py` — FastMCP tools: `cursor_ask`, `cursor_plan`, `cursor_implement`, `cursor_status`
- `src/progress.py` — stream-json event parse + ProgressAggregator (unit-tested)
- `src/jobs.py` — job store under `~/.cache/cursor-headless/jobs/`
- `src/runner.py` — backend switch (`cli` default, opt-in `sdk`)
- `src/cli_runner.py` — stream-json CLI wrapper runner
- `src/sdk_runner.py` — cursor-sdk local runner (MCP uv includes `cursor-sdk`; still needs `CURSOR_API_KEY`)
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

## Backend (`CURSOR_HEADLESS_BACKEND`)

Default **`cli`** (subprocess → `cursor_headless.py` → `cursor-agent --print`).
Opt-in **`sdk`** uses the Python [`cursor-sdk`](https://cursor.com/docs/sdk/python)
package with the same MCP tool surface and envelope.

| Backend | Requires | Notes |
|---------|----------|-------|
| `cli` | `cursor-agent` on PATH | Default; stream-json progress |
| `sdk` | `CURSOR_API_KEY` | MCP uv launch includes `cursor-sdk`; per-call override: MCP `backend="sdk"` |

Set env `CURSOR_HEADLESS_BACKEND=sdk` to make SDK the default for all MCP calls.
CLI-only installs stay working — SDK is lazy-imported.

SDK parity vs CLI flags:

- **`fast` / `*-fast`**: SDK has no `composer-2.5-fast` id. Runner maps
  `prefer_fast` / `*-fast` → `ModelSelection(id=…, params=[fast=true|false])`.
  Grok CLI ids (`cursor-grok-4.5-{low,medium,high}`) map to `grok-4.5` +
  `effort=` + `fast=`.
- **`worktree`**: no native SDK field — runner creates/reuses git worktree at
  `<repo>/.cursor-headless/worktrees/<name>` and sets `LocalAgentOptions(cwd=…)`.
  Worktrees are left on disk (no auto cleanup). Requires a git repo.
- **`force`**: `SendOptions(local=LocalSendOptions(force=True))` for `mode=default` only.
  Older SDKs without `LocalSendOptions` get a prompt fallback instead.
- **CLI-only for now**: `--worktree-base`, `--sandbox`, `--trust`, `--approve-mcps`, `--auto-review`.

## Commands (contributors)

```bash
# Unit tests
python3 -m unittest discover -s src -p 'test_*.py' -v
python3 -m unittest tests.test_cli_runner -v

# Wrapper help / smoke (needs cursor-agent on PATH for real runs)
python3 skills/cursor-headless/scripts/cursor_headless.py --help

# Syntax check
python3 -m py_compile skills/cursor-headless/scripts/cursor_headless.py
uv run --with 'mcp>=1.9,<2' --with cursor-sdk --python 3.14 python -c "import sys; sys.path.insert(0,'src'); import cursor_headless_mcp"
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
