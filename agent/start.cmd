@echo off
REM Shectory LAB optimization agent launcher (used by the Scheduled Task).
REM Runs from the repo root so `trader.lab` imports resolve. Logs to TEMP.
setlocal
set "AGENT_DIR=%~dp0"
set "REPO=%AGENT_DIR%.."
set "PYTHONUTF8=1"
cd /d "%REPO%"
"%AGENT_DIR%.venv\Scripts\python.exe" scripts\opt_agent.py >> "%TEMP%\shectory_opt_agent.log" 2>&1
