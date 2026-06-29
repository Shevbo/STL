@echo off
REM Shectory QUIK file-queue setup. Place next to shectory_trade.lua and run.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_filequeue.ps1"
echo.
pause
