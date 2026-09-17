@echo off
title Project Swing Trades - Unified 5-Stock Live Radar Matrix
color 0B
mode con: cols=105 lines=25
echo ====================================================================
echo        PROJECT SWING TRADES | UNIFIED 5-STOCK LIVE RADAR
echo           Lead Trader: Yashu  |  Quantitative Model: AGY
echo ====================================================================
echo.
echo Starting Multi-Stock Radar Monitor...
echo.

call "%~dp0.venv\Scripts\python.exe" "%~dp0antigravity\daemons\multi_stock_radar.py"

echo.
pause
