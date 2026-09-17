@echo off
title Project Swing Trades - Unified 5-Stock Radar and Telegram Alerts
color 0B
mode con: cols=105 lines=28
echo ====================================================================
echo        PROJECT SWING TRADES | UNIFIED 5-STOCK LIVE RADAR
echo           Lead Trader: Yashu  |  Quantitative Model: AGY
echo ====================================================================
echo.
echo [1/2] Launching Telegram Alert Bot in background...
start /B "" "%~dp0.venv\Scripts\python.exe" "%~dp0antigravity\daemons\telegram_alert_bot.py"
echo [2/2] Starting Unified 5-Stock Live Radar Matrix...
echo.

call "%~dp0.venv\Scripts\python.exe" "%~dp0antigravity\daemons\multi_stock_radar.py"

echo.
pause
