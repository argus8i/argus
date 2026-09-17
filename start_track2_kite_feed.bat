@echo off
title Track 2 Kite Web Live Depth Capture Bridge (Port 9444)
color 0B
echo ====================================================================
echo        TRACK 2: LIQUID MOMENTUM KITE LIVE DATA BRIDGE
echo            Port: 9444 (Strictly Isolated from Track 1 on 9333)
echo            Lead Trader: Yashu - Builder: Antigravity
echo ====================================================================
echo.

set CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe
set PROFILE_DIR=C:\Users\yashw\.chrome_kite_track2_profile

if not exist "%CHROME_EXE%" (
    if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
        set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
        set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    ) else (
        echo [ERROR] Google Chrome could not be found. Please check Chrome installation.
        pause
        exit /b 1
    )
)

echo [1/3] Launching Dedicated Track 2 Chrome on Port 9444...
echo       Profile Directory: %PROFILE_DIR%
start "" "%CHROME_EXE%" --remote-debugging-port=9444 --user-data-dir="%PROFILE_DIR%" --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --no-first-run --no-default-browser-check "https://kite.zerodha.com"

echo.
echo [2/3] Waiting 3 seconds for Chrome (Port 9444) to initialize...
ping -n 4 127.0.0.1 >nul

echo.
echo [3/3] Starting Track 2 Python Bridge Daemon...
echo       Streaming 5-depth order books to shared/track2_liquid/live_depth_track2.json
echo.
call "%~dp0.venv\Scripts\python.exe" "%~dp0antigravity\daemons\track2_kite_bridge.py"

echo.
pause
