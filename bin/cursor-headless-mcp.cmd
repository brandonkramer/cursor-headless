@echo off
setlocal
set "ROOT=%~dp0.."
set "CURSOR_HEADLESS_ROOT=%ROOT%"
cd /d "%ROOT%"
uv run --with mcp>=1.9,<2 --python 3.14 python "%ROOT%\src\cursor_headless_mcp.py"
