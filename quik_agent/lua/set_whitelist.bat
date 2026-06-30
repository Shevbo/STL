@echo off
REM Set the QUIK agent instrument whitelist (must match the STL whitelist). Then restart the agent.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_whitelist.ps1"
echo.
pause
