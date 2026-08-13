---
name: project-roll-aware-pnl
description: Roll-aware per-contract P&L (phantom-profit fix) + deferred live auto-roll
metadata: 
  node_type: memory
  type: project
  originSessionId: 9cca49e6-7297-4434-a4f2-6dfda826405b
---

Rolled robots (e.g. RIM6→RIU6, migrated by DB state-reset) carried their old-contract
position. The UI replayed ALL fills as ONE book → paired a RIM6 sell ~110910 vs a RIU6
buy ~94780 → phantom round-trips. FVG RI robot showed +228528 / ГО ×18 / pos −18; truth
is **+77046 / ×9 / −9** (validated to the ruble 3 ways: scripts/validate_fvg_pnl.py,
`rolledPnl` in frontend/src/lib/lab-analytics.ts, and the real-fill fixture test
lab-analytics.realdata.test.ts). Fix shipped 2026-06-23 (commit d3d15e6): `rolledPnl`
sums P&L per contract, force-closes a carried expired position at its last price, per
contract point value. Backend /live + /showcase now emit per-fill `symbol` +
point_values/initial_margins maps.

**DEFERRED (own branch, before the Dec 2026 U6→Z6 roll):** make the LIVE robot
auto-roll — at expiry force-close all positions on the old contract and reopen the same
volume on the next. Only the P&L *display* model does this today; the trading robot does
not. User explicitly chose to defer (risky live-order change, untestable until Dec). Do
it with a dry-run + tests. See [[reference_forts_contract_roll]].

Chart for a rolled robot is continuous-in-TIME at REAL prices (a visible step at the
roll), all fills from contract 1 shown; position rectangles (not dashed lines), bright
entry / dim AVG arrows. Bars: old contract from ohlcv_bars, current from agent_bars.
