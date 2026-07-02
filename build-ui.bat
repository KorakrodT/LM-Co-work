@echo off
REM Build "LM Co-work.exe" (native window app, LM Studio backend)
REM NOTE: keep this file ASCII-only. Thai text in REM lines breaks cmd
REM parsing on machines where the console codepage mangles UTF-8 bytes.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

echo.
echo [1/6] Closing running app and cleaning old build/junk...
REM Kill a running instance first, otherwise the .exe stays locked
taskkill /f /im "LM Co-work.exe" >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1
REM Remove previous outputs so every build starts clean
rmdir /s /q build >nul 2>&1
rmdir /s /q dist  >nul 2>&1
del /q res.txt *.bak *.bak-* >nul 2>&1

echo.
echo [2/6] Installing PyInstaller and libraries...
py -m pip install --upgrade pyinstaller ruff pytest
py -m pip install -r requirements.txt
if errorlevel 1 goto error

echo.
echo [3/6] Lint (ruff --select F) - catches scope-shadowing bugs like the
echo       "import time" one that broke every launch of main() on 1 Jul 2026...
py -m ruff check --select F server.py tools.py agents.py agent_store.py data_store.py skills_loader.py mcp_client.py knowledge_store.py winproc.py
if errorlevel 1 goto error

echo.
echo [4/6] Compile-check (guard truncated/half-synced .py before building)...
py -m compileall -q server.py tools.py agents.py agent_store.py data_store.py skills_loader.py mcp_client.py knowledge_store.py winproc.py
if errorlevel 1 goto error

echo.
echo [5/6] Smoke tests (AGENTS.md rule: pytest must pass before any build)...
py -m pytest tests/ -q
if errorlevel 1 goto error

echo.
echo [6/6] Building "LM Co-work.exe" ...
REM --add-data "icon.ico;." is required for the runtime taskbar icon fix
py -m PyInstaller --onefile --noconfirm --clean --windowed --name "LM Co-work" --icon "icon.ico" --add-data "index.html;." --add-data "icon.ico;." --collect-all webview --collect-all pythonnet --hidden-import clr server.py
if errorlevel 1 goto error

if exist skills xcopy /e /i /y skills "dist\skills" >nul

echo.
echo ==========================================================
echo  DONE!  File:  %~dp0dist\LM Co-work.exe
echo  Note: no need to open LM Studio - the app auto-starts its
echo        headless server. Just install the CLI once
echo        ( npx lmstudio install-cli ) and have at least one
echo        model downloaded in LM Studio.
echo ==========================================================
echo.
pause
exit /b 0

:error
echo.
echo Build FAILED - please copy the messages above and send them.
echo.
pause
