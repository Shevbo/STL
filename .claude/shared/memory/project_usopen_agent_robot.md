---
name: usopen-agent-robot
description: agent-usopen-RIU6-v1 on agent 9618 (paper); arming to REAL planned 2026-07-15 15:00 MSK; standalone-strategy + bar_offset_min support shipped rev 1784036029
metadata: 
  node_type: memory
  type: project
  originSessionId: c8c08b1b-12e1-4762-8447-d7498bcee44e
---

2026-07-14: `agent-usopen-RIU6-v1` (us_open_fvg) deployed PAPER on agent 9618. Runner rev
1784036029 added: (a) standalone-strategy resolution (`resolve_on_bar` in host.py —
registry first, else import trader.lab.strategies.<id>); (b) `bar_offset_min` param —
runner bars are TRUE UTC, strategies assuming MSK-as-UTC (us_open_fvg) need 180 or the
16:30 anchor lands at 19:30. Params = the showcase robot's swept config, qty edited to 2
by operator (was 7): short-only (allow_long 0), rr 3.6, range 6m, signal window 132m,
stop_pct 3, flatten_eod 23:45 MSK, max_position 7.

Operator plan: arm to REAL 2026-07-15 ~15:00 MSK from the VDS local console (127.0.0.1:8071,
FLAT + typed ID). Arming now RESETS stats (paper->real re-deploy zeroes realized/fills —
host.py change 2026-07-14, supersedes "never reset at arming").

Known cosmetic: «СИГНАЛ СЕЙЧАС» shows "стратегия не найдена" — explain.py has no
standalone-module introspection; add a us_open explainer in the next runner release
(needs strategy STATE plumbed into explain, not just bars/params).

US DST reminder: open_hour=16 valid ~Mar-Nov; switch live robots to 17 in early November.
Related: [[journal-autoheal]], [[live-fvg-robot]].
