@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0antigravity\daemons\tri_agent_monitor.py"
if errorlevel 1 pause

