@echo off
REM Launch Chrome with CDP debugging for shared browser with Claude Code.
REM Double-click this file (do NOT run it from inside Claude Code / VS Code terminal,
REM otherwise Chrome becomes a child process and dies on Claude Code restart).

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --remote-debugging-address=127.0.0.1 ^
  --user-data-dir=C:\chrome-debug ^
  --no-first-run ^
  --no-default-browser-check ^
  https://stl.shectory.ru

echo Chrome launched with CDP on 127.0.0.1:9222
echo Leave this window / Chrome open, then (re)start Claude Code.
timeout /t 3 >nul
