@echo off
REM Shectory LAB optimization agent launcher (used by the Scheduled Task).
REM Runs from the repo root so `trader.lab` imports resolve. Logs to TEMP.
REM Exit code 42 = self-update completed → relaunch with fresh code.
setlocal
set "AGENT_DIR=%~dp0"
set "REPO=%AGENT_DIR%.."
set "PYTHONUTF8=1"
set "OPT_AGENT_WRAPPED=1"
cd /d "%REPO%"
:loop
"%AGENT_DIR%.venv\Scripts\python.exe" scripts\opt_agent.py >> "%TEMP%\shectory_opt_agent.log" 2>&1
if errorlevel 42 goto loop
