# AGENTS.md

**cursor-headless** — thin MCP + skill over `cursor-agent --print` for **Codex**
and **Claude Code**. Parent orchestrates; Cursor workers execute. Python wrapper
+ FastMCP facade; pin `mcp>=1.9,<2`.

Current plugin version: see `.codex-plugin/plugin.json` /
`.claude-plugin/plugin.json` (keep both in sync).

## Layout

- `src/cursor_headless_mcp.py` — FastMCP tools: local `cursor_ask` / `cursor_plan` /
  `cursor_implement`, cloud `cursor_cloud_plan` / `cursor_cloud_review` /
  `cursor_cloud_implement`, plus `cursor_status`
- `src/progress.py` — stream-json event parse + ProgressAggregator (unit-tested)
- `src/jobs.py` — job store under `~/.cache/cursor-headless/jobs/`
- `src/runner.py` — backend switch (auto `sdk` when `CURSOR_API_KEY` set; else `cli`)
- `src/cli_runner.py` — stream-json CLI wrapper runner
- `src/sdk_runner.py` — cursor-sdk local runner (MCP uv includes `cursor-sdk`; still needs `CURSOR_API_KEY`)
- `src/sdk_cloud_runner.py` — cursor-sdk cloud VM runner (`delivery=findings|pr_review` on review)
- `src/pr_review_publish.py` / `src/pr_diff.py` — GitHub PR review POST via host `gh` (not cloud token)
- `src/sdk_bridge_patch.py` — Windows Bridge discovery patch (WinError 10038)
- `skills/cursor-headless/scripts/cursor_headless.py` — CLI wrapper (what MCP shells)
- `skills/cursor-headless/SKILL.md` — routing + parent process (source of truth for behavior)
- `commands/` — `/cursor-implement`, `/cursor-review`, `/cursor-loop`
- `workflows/` — Claude Code dynamic workflows (`implement.js`, `review.js`)
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

## Backend selection

Resolution order:

1. Per-call MCP `backend="cli"|"sdk"`
2. Env `CURSOR_HEADLESS_BACKEND=cli|sdk`
3. **Auto:** `sdk` if `CURSOR_API_KEY` is set **in the MCP process env**, else `cli`

| Backend | Requires | Notes |
|---------|----------|-------|
| `cli` | `cursor-agent` on PATH (login) | Works without API key; stream-json progress |
| `sdk` | `CURSOR_API_KEY` in MCP process | MCP uv includes `cursor-sdk`; auto when key set |

Windows: `sdk_bridge_patch` replaces upstream Bridge `selectors.select` discovery
(WinError 10038) with a sleep-poll drain so live SDK works.

SDK is lazy-imported — CLI-only installs stay working without a key.

### `CURSOR_API_KEY` for Codex desktop (SDK)

The plugin reads **only** `os.environ["CURSOR_API_KEY"]` inside the MCP server.
It does not call Pass/1Password, and it does not re-read `config.toml` after start.

**Preferred (Codex desktop):** put the key in the desktop env file, whitelist
forwarding into MCP, then restart:

```text
# macOS / Linux
~/.codex/.env

# Windows
%USERPROFILE%\.codex\.env
```

```dotenv
CURSOR_API_KEY=crsr_…
```

Mint at [Dashboard → API Keys](https://cursor.com/dashboard/api). Never commit
`.env`. Never print the key. `chmod 600` on POSIX.

Codex distinguishes **`env`** (literal values set on the server) from
**`env_vars`** (names forwarded from the parent / `.env`). This plugin’s
manifest sets UTF-8 via `env` and whitelists `CURSOR_API_KEY` via `env_vars`.
If an older install lacks that whitelist, add:

```toml
[plugins."cursor-headless@cursor-headless".mcp_servers.cursor-headless]
env_vars = ["CURSOR_API_KEY"]
```

Then **fully quit and reopen** the Codex desktop app (not just a new chat).
Saving `config.toml` / `.env` does **not** update already-running Codex/MCP
processes.

**Also useful:**

| Mechanism | Notes |
|-----------|--------|
| `~/.codex/.env` | Holds the secret for Codex desktop |
| Plugin / config `env_vars = ["CURSOR_API_KEY"]` | **Required** so desktop forwards `.env` into this MCP subprocess |
| User/OS env `CURSOR_API_KEY` | Helps CLI shells; still restart Codex for desktop MCP |
| `mcp_servers.*.env` with a literal key | Avoid — keeps secrets in `config.toml`; desktop may ignore plugin `env` overrides anyway |
| Plugin manifest `env` (`PYTHONUTF8`, …) | Non-secrets only |
| `pass-cli run --env-file … -- codex` | Fine for CLI-launched Codex; desktop still wants `.env` + `env_vars` |

**CLI-launched hosts / password managers:**

```bash
# cursor.env — reference only (no secret in git)
# CURSOR_API_KEY=pass://Keys/Cursor/password
pass-cli run --env-file cursor.env -- codex
```

Bare `pass://` in plugin JSON is not resolved.

**Diagnose SDK “key missing”:**

1. Confirm `~/.codex/.env` has a `crsr_…` line
2. Confirm `env_vars` includes `CURSOR_API_KEY` (plugin ≥ 0.3.12 or config override)
3. Fully restart Codex desktop, then retry `cursor_ask` (auto or `backend="sdk"`)
4. If you only need workers now: `backend="cli"` after `cursor-agent login`

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
