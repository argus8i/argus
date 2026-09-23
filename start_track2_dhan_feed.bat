@echo off
cd /d "%~dp0"
title Track 2 DhanHQ WebSocket Live Feed Bridge
color 0A
echo ====================================================================
echo        TRACK 2: HEADLESS DHANHQ WEBSOCKET DATA BRIDGE
echo            Protocol: Binary WebSocket v2 // Sub-10ms Feeds
echo            Rule 1 Gate: 100%% Paper Trading (Observation Only)
echo            Lead Trader: Yashu - Builder: Antigravity
echo ====================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found in .venv.
    pause
    exit /b 1
)

echo [1/2] Checking Dhan configuration...
if not exist "antigravity\config\dhan_config.json" (
    echo [WARNING] antigravity\config\dhan_config.json not found.
    echo           Creating template from dhan_config.json.example...
    copy "antigravity\config\dhan_config.json.example" "antigravity\config\dhan_config.json" >nul
)

echo.
echo [2/2] Starting DhanHQ WebSocket Bridge Daemon...
echo       Streaming quotes/depth to shared\track2_liquid\live_depth_track2.json
echo       Streaming heartbeat to shared\track2_liquid\dhan_feed_heartbeat.json
echo.

call "%~dp0.venv\Scripts\python.exe" -m antigravity.daemons.dhan_feed_bridge

echo.
pause
