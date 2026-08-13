---
name: vdsguard
description: QUIK-hang watchdog (pong-based, forced info.exe restart) + VDS RAM health; rev 1784105695: + self-registering logon autostart (agent writes start_all.bat, schtasks /F)
metadata:
  type: project
---

Shipped 2026-07-15 rev 1784102947: internal/vdsguard. Pong-based (Lua ping 5s, market-
independent): SLOW (pong>15s or rtt>10s) -> WARN alert throttled 10m; HUNG (pong>300s) ->
CRITICAL alert + taskkill info.exe + relaunch from pong-reported QuikFolder, cooldown 900s,
never blind (unknown folder / never-ponged session -> alert only). RAM via
GlobalMemoryStatusEx: VDS_LOW_MEMORY when <400MB avail or >=92% load, throttled 30m.
Status page health.vds block (mirrored to STL). Config: quik_guard_disabled /
quik_guard_hung_sec / quik_guard_cooldown_sec in agent_config.json.

STRICT operator workflow: before manual Lua/terminal servicing set quik_guard_disabled
or stop the agent — else the guard restarts QUIK mid-servicing after hung_sec.

Autostart self-registers since rev 1784105695 (ShectoryTradeStack logon task: QUIK -> 25s
-> agent; gate autostart_disabled). Operator-confirmed: auto-connect flag ON, layout saved
(covers Lua autoload), w32tm task done. Open: Windows auto-logon after reboot; QUIK is
key-based (Finam, C:\QuikFinam) so the key password prompt stays manual after any QUIK
start - guard restart raises the window + CRITICAL alert, operator types the password.
Agent dir C:\distr\dist, exe quik-agent_amd64.exe (old quik-agent.exe of 29.06 is a dead
leftover). Delivery rule honored: files reach the VDS through the agent, never downloads. Bars/books survive via
runner_state.json (proven live: bars=27 across restart).
Related: [[restart-immunity]], [[journal-autoheal]].
