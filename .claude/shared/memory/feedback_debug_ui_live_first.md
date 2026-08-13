---
name: feedback-debug-ui-live-first
description: "For live UI/CSS/layout bugs, inspect the running DOM via the browser FIRST - do not blind-fix and redeploy"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a3701a33-3d27-427a-aa8b-04e822e31829
---

STRICT process rule (Boris, sharply, 2026-06-18): do NOT burn time/tokens blind-fixing a UI bug by reasoning + deploy + "try again". For any live frontend/layout/CSS/drag bug, **attach to the browser and inspect the real DOM FIRST**.

**Why:** a "border won't drag" bug took several blind deploys and a huge token spend. The real cause was only visible in the live DOM: the chart `.frame` didn't fill its container (`flex:1` missing), leaving a ~280px empty gap; the user was grabbing the chart's bottom edge, not the resize handle which sat at the bottom of the gap. One `getBoundingClientRect` dump found it in one shot.

**How to apply:** Playwright MCP is wired to Boris's Chrome via CDP `127.0.0.1:9222` ([[reference-shared-browser]]). Connect, navigate, and `browser_evaluate` to read `getBoundingClientRect()` / `elementFromPoint()` / computed styles / simulate the interaction BEFORE changing code. Gotchas: (1) the CDP Chrome may not be logged into stl (`/api/auth/me` 401 → only the login screen renders, no `.shell`) — ask Boris to log in there, or mint a session via the keymaster bridge secret. (2) Playwright init can hang if a stale tab/service-worker is open; close offending CDP targets first. (3) Svelte 5 flushes DOM async — await a rAF before measuring after a simulated event. Related: [[feedback-secrets-protocol]].
