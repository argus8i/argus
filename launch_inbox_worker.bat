@echo off
setlocal
cd /d "%~dp0"
echo [LAUNCHER] Starting Antigravity Supervised Inbox Worker Daemon...
call .venv\Scripts\activate.bat
python antigravity\daemons\supervised_inbox_worker.py
pause
