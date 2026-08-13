---
name: restart-immunity
description: full restart/failure immunity map of the robot stack; bars persistence + orphan-runner kill shipped rev 1784101365 (2026-07-15)
metadata:
  type: project
---

Shipped 2026-07-15 rev 1784101365: (a) closed-bars tail (600/robot) persists in
runner_state.json, re-seeds on deploy, last_bar_run seeded so a restored bar is never
re-executed; BarBuilder drops trades at/behind the newest closed bar. (b) self-update
.bat taskkills robot-runner.exe before the companion copy (orphan + locked-exe fix).

Immunity map: specs+paused = robots.json (replayed on connect); book+fills+bars =
runner_state.json (atomic); lost fills = journal auto-heal from QUIK tables; phantom
orders = reconcileStalePending both sides; STL down = robots keep trading; Lua restart =
cmd offset seek-to-end + table re-publish; agent restart = .bat kills runner, new pair
re-warms from state.

NOT covered (operator-side): VDS reboot needs agent autostart (service/scheduled task) +
QUIK autologin + Lua autostart entry — confirm with operator; no SSH to VDS.
**Why:** live robots must survive any single-host failure without operator surgery.
**How to apply:** after any restart check bars_count grows and recon OK; books must carry
over. Related: [[journal-autoheal]], [[runner-fill-crash-cp1251]], [[usopen-agent-robot]].
