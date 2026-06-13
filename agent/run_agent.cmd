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

REM A Scheduled Task / Startup launch may not inherit user env vars added mid-session.
REM Pull them straight from the persistent user environment (HKCU\Environment) if unset.
REM (Reads the EXISTING value — stores no secret anywhere new.)
if not defined OPT_AGENT_TOKEN for /f "tokens=2,*" %%a in ('reg query "HKCU\Environment" /v OPT_AGENT_TOKEN 2^>nul ^| find "REG_SZ"') do set "OPT_AGENT_TOKEN=%%b"
if not defined STL_API for /f "tokens=2,*" %%a in ('reg query "HKCU\Environment" /v STL_API 2^>nul ^| find "REG_SZ"') do set "STL_API=%%b"

REM Prevent machine sleep while the agent is running (silent; no-op if already off).
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change standby-timeout-dc 0 >nul 2>&1
powercfg /change monitor-timeout-ac 0 >nul 2>&1

:loop
REM fixed log path (task %TEMP% differs from your shell's), findable at agent\agent.log
agent\.venv\Scripts\python.exe scripts\opt_agent.py --log "%~dp0agent.log" %*
if %ERRORLEVEL%==42 (
  echo [wrapper] self-update applied - restarting agent...
  REM ping as a headless-safe sleep (timeout needs a console)
  ping -n 3 127.0.0.1 >nul 2>&1
  goto loop
)
echo [wrapper] agent exited (code %ERRORLEVEL%) - not restarting.
endlocal
