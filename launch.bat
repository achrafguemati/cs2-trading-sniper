@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================================================
echo   CS2 Trading Sniper - Local Windows 11 Launcher (Strictly Read-Only)
echo ========================================================================

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo [Setup] Created .env from .env.example. Edit .env to add Discord/Telegram keys.
    )
)

set "PY_EXE="
if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"
if not defined PY_EXE (
    if exist ".venv313\Scripts\python.exe" set "PY_EXE=.venv313\Scripts\python.exe"
)
if not defined PY_EXE (
    where python >nul 2>&1
    if !errorlevel! equ 0 set "PY_EXE=python"
)

if not defined PY_EXE (
    echo [Error] Python 3.12+ was not found on PATH and no .venv exists.
    pause
    exit /b 1
)

echo [Backend] Ensuring Python package is installed...
"%PY_EXE%" -c "import fastapi, uvicorn, sniper.api.app" >nul 2>&1
if errorlevel 1 (
    "%PY_EXE%" -m pip install -e .
    if errorlevel 1 (
        echo [Error] Failed to install Python dependencies.
        pause
        exit /b 1
    )
)

if not exist "frontend\dist\index.html" (
    where npm >nul 2>&1
    if !errorlevel! equ 0 (
        echo [Frontend] Building React/TypeScript/Vite dashboard bundle...
        pushd frontend
        if not exist "node_modules" (
            call npm install
        )
        call npm run build
        popd
    ) else (
        echo [Warning] npm not found and frontend\dist missing. Launching API-only server.
    )
)

set "SNIPER_HOST=127.0.0.1"
set "SNIPER_PORT=8000"
set "SNIPER_URL=http://%SNIPER_HOST%:%SNIPER_PORT%"

echo [Launch] Starting FastAPI dashboard server at %SNIPER_URL% ...
start "CS2 Trading Sniper Server" "%PY_EXE%" -m sniper.cli serve --host %SNIPER_HOST% --port %SNIPER_PORT%

REM Wait briefly for /api/health before launching browser
set /a TRIES=0
:wait_loop
set /a TRIES+=1
"%PY_EXE%" -c "import urllib.request, sys; urllib.request.urlopen('%SNIPER_URL%/api/health', timeout=1); sys.exit(0)" >nul 2>&1
if !errorlevel! equ 0 goto open_browser
if !TRIES! GEQ 20 goto open_browser
timeout /t 1 /nobreak >nul
goto wait_loop

:open_browser
echo [Ready] Opening %SNIPER_URL% in your default browser...
start "" "%SNIPER_URL%"
exit /b 0
