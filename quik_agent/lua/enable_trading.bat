@echo off
REM Arm the QUIK agent for trading (sets quik_trading_enabled=true). Then restart the agent.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_trading.ps1"
echo.
pause
