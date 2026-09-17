@echo off
title Kite Web Live Depth Capture Bridge
color 0A
echo ====================================================================
echo        KITE WEB AUTOMATED LIVE MARKET DATA BRIDGE
echo            Lead Trader: Yashu - Builder: Antigravity
echo ====================================================================
echo.

set CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe
set PROFILE_DIR=C:\Users\yashw\.chrome_kite_profile

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

echo [1/3] Launching Google Chrome with DevTools Remote Debugging on Port 9333...
echo       Profile Directory: %PROFILE_DIR%
start "" "%CHROME_EXE%" --remote-debugging-port=9333 --user-data-dir="%PROFILE_DIR%" --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --auto-open-devtools-for-tabs --no-first-run --no-default-browser-check "https://kite.zerodha.com"

echo.
echo [2/3] Waiting 3 seconds for Chrome to initialize...
ping -n 4 127.0.0.1 >nul

echo.
echo [3/3] Starting Antigravity Python Bridge Daemon...
echo       Streaming 5-depth order books and volume to shared/live_depth.json
echo.
call "%~dp0.venv\Scripts\python.exe" "%~dp0antigravity\daemons\kite_web_depth_bridge.py"

echo.
pause
