@echo off
REM Supervised launcher for the Shectory optimization agent (Windows).
REM The agent self-updates on command and exits with code 42 to request a restart;
REM this wrapper relaunches it with the freshly-downloaded code. Console output stays
REM here. Stop with Ctrl+C (answer "N" to "Terminate batch job").
REM
REM Usage (from the repo root):  agent\run_agent.cmd [--insecure] [--workers 16] ...
setlocal
cd /d "%~dp0.."
set OPT_AGENT_WRAPPED=1

:loop
agent\.venv\Scripts\python.exe scripts\opt_agent.py %*
if %ERRORLEVEL%==42 (
  echo [wrapper] self-update applied - restarting agent...
  REM ping as a headless-safe sleep (timeout needs a console)
  ping -n 3 127.0.0.1 >nul 2>&1
  goto loop
)
echo [wrapper] agent exited (code %ERRORLEVEL%) - not restarting.
endlocal
