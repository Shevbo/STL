---
name: reference-shared-browser
description: "Shared browser setup — Playwright MCP connected to user's Chrome via CDP"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
---

**«Единый браузер» = это рабочий термин Бори.** Значит: ОДИН Chrome, в который смотрим
оба — и Боря, и я (Claude). Не нужно обмениваться скриншотами: я вижу ту же вкладку, что
и он, в реальном времени. И он, и я можем кликать/печатать на сайте в этом же окне через
Playwright MCP (CDP). Когда Боря говорит «давай единый браузер» — он хочет, чтобы я работал
в его живом Chrome, а не в отдельном headless-окне.

Shared browser: user and Claude drive ONE Chrome window.

**Config (already applied):** Playwright MCP active config at
`C:\Users\Boris\.claude\plugins\cache\claude-plugins-official\playwright\unknown\.mcp.json`
has args `["@playwright/mcp@latest", "--cdp-endpoint", "http://localhost:9222"]`.
(Marketplace source copy was NOT edited — user rejected that; only the active cache file.)

**To start a shared session:**
1. Launch Chrome with CDP BEFORE Claude Code session starts:
   `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList '--remote-debugging-port=9222','--user-data-dir=C:\chrome-debug','--no-first-run','--no-default-browser-check','https://stl.shectory.ru'`
2. Verify CDP: `Invoke-WebRequest http://localhost:9222/json/version`
3. Playwright MCP tools then drive THAT window (not a separate browser).

**Verify connection:** navigate Playwright to `...?probe=N`, then check
`Invoke-WebRequest http://localhost:9222/json` — if the probe URL shows in the tab list, it's shared.

**CRITICAL gotcha:** Do NOT launch Chrome via PowerShell/Bash tool from inside Claude Code —
it becomes a CHILD process of VS Code and gets KILLED on every Claude Code restart, so by the
time MCP reconnects, 9222 is dead (ECONNREFUSED). User must launch Chrome INDEPENDENTLY:
double-click `C:\Dev\Shectory Trade & Lab\start-chrome-debug.bat` (start-detached), THEN restart Claude Code.

**Also:** use `127.0.0.1` not `localhost` in --cdp-endpoint (localhost may resolve to IPv6 ::1
while Chrome listens on IPv4). Chrome launched with --remote-debugging-address=127.0.0.1.

Order that works:
1. Double-click start-chrome-debug.bat (independent process)
2. Restart Claude Code (MCP reads cdp-endpoint at startup, connects to the live Chrome)
3. Verify: Playwright navigate ...?probe=N, check http://127.0.0.1:9222/json shows that URL.

See [[project-lab-mvp-state]].
