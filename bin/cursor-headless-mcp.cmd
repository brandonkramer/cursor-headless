@echo off
setlocal
set "ROOT=%~dp0.."
set "CURSOR_HEADLESS_ROOT=%ROOT%"
rem Force UTF-8 so Cursor output with em-dashes / fancy quotes does not die on CP-1252.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"
uv run --with mcp>=1.9,<2 --python 3.14 python "%ROOT%\src\cursor_headless_mcp.py"
