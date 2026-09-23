@echo off
setlocal
title Track 2 Market Field Test
cd /d "%~dp0"

echo TRACK 2 MARKET FIELD TEST - REVIEW PENDING
echo Captures Kite inputs and ORB candidate decisions. Does not submit paper or real orders.
echo Start by 08:45 IST; official preflight must finish before 09:00 IST.
echo Same-day restart reuses verified preflight. Login to Kite if asked.
echo Summary is saved under shared\track2_liquid\field_tests\DATE\summary.json.
echo.

echo [1/3] Starting the Track 2 Control Room...
start "Track 2 Control Room" cmd /k call ".venv\Scripts\python.exe" -m antigravity.daemons.track2_live_monitor --open-browser

echo [2/3] Starting the dedicated Kite feed...
start "Track 2 Kite Feed" cmd /k call "%~dp0start_track2_kite_feed.bat"
echo Waiting 10 seconds for Chrome and the feed bridge...
ping -n 11 127.0.0.1 >nul

echo [3/3] Starting the field-test decision recorder...
call ".venv\Scripts\python.exe" -m antigravity.daemons.track2_daily_paper_desk
exit /b %errorlevel%
