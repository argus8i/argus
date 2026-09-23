@echo off
setlocal
title Track 2 Phase 1D Non-Counting Rehearsal
cd /d "%~dp0"

echo TRACK 2 PHASE 1D - PAPER-ONLY, NON-COUNTING REHEARSAL
echo This launcher cannot place broker orders or increment qualification counters.
echo.
echo Prerequisites: start before 09:00 IST, log into Kite, and keep all configured
echo Track 2 candidates visible in the dedicated watchlist.

echo [1/3] Fetching and sealing official NSE policy circulars...
call ".venv\Scripts\python.exe" -m antigravity.daemons.track2_policy_ingestor
if errorlevel 1 exit /b 1

echo [2/3] Starting the dedicated Track 2 Chrome/CDP feed...
start "Track 2 Kite Feed" cmd /k call "%~dp0start_track2_kite_feed.bat"

echo [3/3] Waiting up to 10 minutes for a fresh valid feed, then starting rehearsal...
call ".venv\Scripts\python.exe" -m antigravity.daemons.track2_rehearsal_runner ^
  --config "shared\track2_liquid\rehearsal_config.json" ^
  --band-policy "shared\track2_liquid\band_policy\band_policy.json" ^
  --wait-feed-seconds 600
exit /b %errorlevel%
