@echo off
title ARGUS 8i // NIGHTWATCH MASTER LAUNCHER
color 0A

echo ===============================================================================
echo                ARGUS 8i // NIGHTWATCH CONTROL ROOM (PORT 8767)
echo             Track 2: BEACON Liquid Momentum & Risk Architecture
echo                         Project Swing Trades
echo ===============================================================================
echo.
echo [*] Checking Python Virtual Environment...
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo [*] Starting NIGHTWATCH Terminal Server on port 8767...
start /b "" "%PYTHON_EXE%" antigravity\daemons\track2_terminal_server.py --port 8767 --corpus 250000

timeout /t 2 /nobreak >nul

echo [*] Opening Browser to NIGHTWATCH Terminal...
start http://127.0.0.1:8767/

echo.
echo [✓] ARGUS 8i is operational at http://127.0.0.1:8767/
echo [!] Keep this window open or minimize it. Press Ctrl+C to stop.
echo ===============================================================================
"%PYTHON_EXE%" -c "import time; time.sleep(86400)"
