---
name: runner-fill-crash-cp1251
description: "2026-07-13 incident — '→' in FILL log killed runner on every REAL fill; book froze, robot re-emitted orders all day; fix in b7fefe1, rev 1783974444"
metadata: 
  node_type: memory
  type: reference
  originSessionId: c8c08b1b-12e1-4762-8447-d7498bcee44e
---

2026-07-13: rev 1783928193 (per-robot event log) added console FILL line with '→' (U+2192).
Runner stdout = pipe to agent; RU-Windows pipe = cp1251 STRICT → UnicodeEncodeError on every
REAL fill. Real path (consume_events) had no catch → gather killed the runner BEFORE persist →
fill lost, supervisor restart with stale book → strategy re-emitted on every confirmed signal
(8 sells + 4 buys unrecorded on agent-fvg-RIU6-v2; true SHORT 3 vs believed LONG 1). Paper
robots masked it (exception caught in tick_robot) — matches the CLAUDE.md warning that paper
hides real-path bugs.

Diagnostic signatures (remote, no VDS access needed):
- mirror recent_fills frozen while quik.orders (agent-local-status) shows fresh tagged fills;
- paired same-second same-side orders = reversal close+open re-emitted;
- averaging orders spaced ~22 min = ATR-warmup after each crash-restart;
- bars_count much lower than process-age minutes = runner restarted recently;
- NO QUIK_NO_REPLY alerts = manager/Lua path healthy, loss is agent→runner or inside runner.

Fix (commit b7fefe1, shipped rev 1783974444, untriggered → 03:00 apply):
main.py stdout/stderr reconfigure utf-8 backslashreplace; runtime.event() console print
try/except; host.consume_events per-event try/except. Regression test in
tests/runner/test_agent_runtime.py.

**Why:** logging must never sit unprotected in the trade path; RU VDS pipes are cp1251.
**How to apply:** any new runner console output must survive strict-encoding stdout; keep
non-ASCII out of hot-path log lines or rely on the utf-8 reconfigure staying in main.py.
Related: [[agent-zombie-traps]], [[agent-robot-pnl]].
