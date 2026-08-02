#!/usr/bin/env python3
"""Debug Windows fake-agent integration for cli_runner."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from cli_runner import run_cli  # noqa: E402
from test_cli_runner import _write_fake_agent  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        events = [
            {"type": "system", "subtype": "init", "model": "fake-model", "session_id": "abc"},
            {"type": "result", "subtype": "success", "result": "hello world", "duration_ms": 50},
        ]
        serialized = [json.dumps(e) for e in events]
        _write_fake_agent(
            bin_dir,
            body=f"""\
            #!/usr/bin/env python3
            import sys
            for line in {serialized!r}:
                print(line, flush=True)
            sys.exit(0)
            """,
        )
        print("files:", [p.name for p in bin_dir.iterdir()])
        cmd = bin_dir / "cursor-agent.cmd"
        if cmd.is_file():
            print("cmd contents:\n", cmd.read_text(encoding="utf-8"))
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        with patch.dict(os.environ, env, clear=True):
            print("which:", shutil.which("cursor-agent"))
            # Direct invoke fake
            if cmd.is_file():
                import subprocess

                direct = subprocess.run(
                    [str(cmd)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                print("direct exit", direct.returncode)
                print("direct out", direct.stdout[:500])
                print("direct err", direct.stderr[:500])
            result = run_cli(
                prompt="say hello",
                cwd=tmp,
                mode="ask",
                model="cursor-grok-4.5-high",
                prefer_fast=False,
                force=False,
                worktree=None,
                skip_preflight=True,
                continue_session=False,
                timeout=30.0,
                require_diff=False,
            )
        print(json.dumps(result, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
