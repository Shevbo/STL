---
name: reference-commission-model
description: "FORTS commission model in STL — taker for backtests, maker for live; rates by instrument group"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
---

FORTS commission, shipped 2026-06-04 (replaces the old flat 4 RUB/order).

**Rule from user:** backtests model TAKER (conservative); live trading models MAKER.
- TAKER (market / cross spread): MOEX exchange fee + broker fee.
- MAKER (limit resting in book): broker fee ONLY, no exchange fee.

**Broker:** Finam base tariff = 0.45 RUB per contract, flat, per fill.

**MOEX taker fee = group_rate × notional**, notional = price × point_value × qty. Maker = 0.
Group rates (fraction of notional): fx 0.0000462, index 0.0000660, stock 0.0001980,
commodity 0.0001320, rate 0.0001650. Ticker 2-letter prefix → group (RI/MX=index,
SI/EU=fx, GZ/SR=stock, BR/GD=commodity); unknown → index default.

**Code:**
- backend: `trader/lab/commission.py::commission_for(symbol, price, qty, point_value, taker)`.
  BacktestRuntime.place_order + compute_metrics use taker=True. LiveRuntime = maker.
- frontend: `frontend/src/lib/lab-analytics.ts::commissionFor(...)` mirrors it; `tradeEvents`
  takes (symbol, taker). BacktestChart has a `taker` prop (default true); RobotWindow passes
  taker={false} (live = maker).

RTS/RI is force-included in every campaign: `enqueue_campaign.py` ALWAYS_ASSETS=["RTS"] →
top_instruments(always=...) appends the RTS front contract even if outside top-N.

**STALE-DATA PURGE (2026-06-05):** optimization_leaderboard had mixed-commission rows → a row could
show phantom profit (e.g. RIM6 roc +7969 stale vs −14371 real taker). Cause: rows computed before the
taker-commission rework. PURGED: (1) all non camp-/opt- campaigns = old in-process optimize_campaign.py
(17539 rows); (2) camp-20260604-1023 (4861 rows) — computed 06-04 morning BEFORE the i9 got the taker
commission files. KEPT (reproduction-verified, run_single_backtest with current code over the campaign
window matches the stored net_profit): camp-20260604-1748 (taker), opt-20260605-0618 (taker, exact match).
VERIFY METHOD when leaderboard trust is in doubt: SELECT a top row, recompute it with current code over
02.03–24.05, compare net_profit; mismatch >~2% = stale-commission row, purge that campaign. The drill-in
chart now reproduces the leaderboard's EXACT period (not to-yesterday) so chart == table.

See [[reference-vds-load]], [[project-lab-mvp-state]].
