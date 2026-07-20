# MAE + OOS/walk-forward + fast<slow filter, and a live GDU6 re-run

Date: 2026-07-20
Status: approved (design), pending implementation plan

## Problem

Campaign `opt-20260719-2235` (macd_cross, GDU6-only) produced RF values of 626-1012 that
were a mirage. Three defects in how we score and sweep:

1. **RF ignores open-position drawdown.** `BacktestRuntime._equity`
   (`trader/lab/runtime.py`) only moves on a fill: commission on every fill, realized P&L
   on a close (lines ~129/145/147). It is NOT marked-to-market per bar. So the per-bar
   `equity_curve` built in `run_one_backtest` (`trader/lab/backtest.py` ~L191-197) is flat
   while a position is open, and `compute_metrics` measures `max_drawdown` only over the
   equity curve of CLOSED round-trips (~L148-151). A no-stop averaging strategy that draws
   down 10x ГО unrealized before grinding back to a small TP shows a tiny drawdown and a
   huge RF. The risk it actually ran is invisible.

2. **No out-of-sample check.** The whole history is optimized and reported as one number,
   so a config curve-fit to one contract/period (GDU6) looks as good as a robust one.

3. **Degenerate configs waste compute.** macd_cross with `fast == slow` yields a zero MACD
   line (no signal); `fast > slow` is inverted. Both are swept today.

## Goals

- Expose the true risk of open-position averaging (MAE + mark-to-market drawdown).
- Catch overfitting cheaply (out-of-sample split + window consistency).
- Never sweep degenerate macd configs.
- Re-run the GDU6 macd_cross campaign with random-sampled configs feeding a LIVE
  hit-parade the operator can watch and DEPLOY from mid-run; the whole run must finish
  overnight (not week-scale CPU).

Non-goals: changing live/paper execution; per-window re-optimization (classic
walk-forward-optimization); touching strategies other than macd_cross for this run.

## Design

### Component 1 — MAE + mark-to-market drawdown (backtest engine)

Pure observation added to the bar loop of `run_one_backtest` (`trader/lab/backtest.py`).
Each bar, after `on_bar`, read the open position from `runtime` (`_positions[symbol]` →
side/qty/avg, already maintained) and the current `bar.close`, and compute the
open-position unrealized P&L in RUB:

    unreal = signed_qty * (bar.close - avg) * point_value      # signed_qty <0 for short

Track across the run:
- `mtm_equity = runtime._equity + unreal` per bar → the mark-to-market curve.
- `max_drawdown_mtm` — max peak-to-trough on the mtm curve, in RUB (the TRUE drawdown).
- `max_mae` — the most-negative `unreal` seen while a position was open, in RUB (worst
  single adverse excursion; 0.0 if never underwater). Reported as a POSITIVE magnitude.

Decision (pins the one ambiguity): the **bar loop computes** `max_mae` and
`max_drawdown_mtm` (it is the only place with per-bar unrealized), and
`run_one_backtest` merges them plus `recovery_factor_mtm` into the returned dict
(`{**compute_metrics(...), "max_mae": ..., "max_drawdown_mtm": ..., "recovery_factor_mtm":
...}`). `compute_metrics` itself stays trades-only, but its `empty` dict gains these three
keys defaulted (`max_mae: 0.0`, `max_drawdown_mtm: 0.0`, `recovery_factor_mtm: None`) so
every result has a consistent shape. New fields:
- `max_mae` (RUB, positive magnitude; 0.0 if never underwater)
- `max_drawdown_mtm` (RUB, positive magnitude)
- `recovery_factor_mtm = net_profit / max_drawdown_mtm` (None when dd == 0)
- keep existing `recovery_factor` (rename its LABEL in the UI to "closed-trade RF"; the
  field name stays for back-compat).

`max_mae` / `max_drawdown_mtm` are also expressible as % of `margin_used` for display; the
engine returns RUB, the UI derives the %.

No strategy-code change. Parity note: this is backtest-only scoring; it does not need a
mirror in `robot_runner`.

### Component 2 — fast<slow filter (sampler)

In the param sampler (`scripts/queue_campaign.py`, `_build_grid` / the random-sample path)
drop any macd_cross combo where `fast >= slow` before the job is queued. Applies to
macd_cross only (a strategy whose schema has both `fast` and `slow`); other strategies
unaffected. A one-line predicate + a `log()` of how many combos were dropped (no silent
truncation).

### Component 3 — walk-forward scoring (per config, one backtest)

Each sampled config runs the full GDU6 history ONCE (configs are random, not re-optimized
per window, so no K-fold compute blow-up). Metrics are then SLICED by time from the
already-collected trades + mtm curve:
- **IS/OOS 70/30**: first 70% of the span = in-sample, last 30% = out-of-sample. Report
  `net_OOS`, `recovery_factor_mtm_OOS`, `max_mae_OOS`, and `degrade = net_OOS_rate /
  net_IS_rate` (per-bar-normalized so the 70/30 length difference cancels).
- **Window consistency**: split the span into W equal windows (default W=4, ~quarterly for
  a contract's life); count `windows_profitable / W`. A config that only prints in one
  window scores low here even if overall RF looks great.

Slicing lives in a small pure helper `window_metrics(trades, mtm_curve, span, splits)` so
it is unit-testable without running a backtest.

### The campaign

- `queue_campaign.py`: random no-repeat sample of M macd_cross configs on **GDU6**,
  `--pin qty=1`, fast<slow enforced (Component 2), `--include-avg-params` for the averaging
  axes. M is calibrated so M single-history backtests finish overnight on the i9
  (measure current per-backtest wall time on GDU6 first; target < ~8h of i9 wall,
  ProcessPoolExecutor across its cores). Explicit `paramSets` (random sample) bypasses the
  grid product and the local combo cap (engine=remote), per existing behavior.
- Results stream to the Botstore `optimization_leaderboard` live as each config completes
  (existing `camp-`/`opt-` mirroring + the i9 `leaders` already surfaced in the heartbeat).
  New columns: `RF_mtm_OOS`, `max_mae`, `windows_profitable/W`, `degrade%`.
- **Ranking**: order by `recovery_factor_mtm_OOS` (honest RF, out-of-sample), with
  `max_mae` and `windows_profitable/W` shown as guardrails so a high-RF/high-MAE or
  single-window curve-fit ranks low even at a high raw RF.
- **Deploy from mid-run**: each leaderboard row gets a button that copies its params into a
  new PAPER robot (reuse the existing clone-to-paper deploy path) so the operator grabs a
  config without waiting for the campaign to finish.

## Data flow

    queue_campaign (random sample, fast<slow)
        -> backtest_runs (engine=remote, run_id camp-…)
        -> i9 pull agent claims -> run_one_backtest (MAE/mtm in the loop)
              -> window_metrics slices IS/OOS + W windows
        -> results POSTed back -> optimization_leaderboard (Botstore)
        -> live leaderboard (RF_mtm_OOS ranked; MAE + consistency guardrails)
              -> operator deploy button -> paper robot

## Testing

- `window_metrics`: pure unit tests — a synthetic trade list + mtm curve, assert IS/OOS
  split points, degrade, and windows_profitable counts.
- MAE/mtm: a hand-built bars+trades fixture where a long averages into a deep unrealized
  hole then closes at a small profit; assert `max_mae` and `max_drawdown_mtm` are large
  while the old `max_drawdown` (closed-pairs) stays small — the exact mirage, pinned.
- fast<slow filter: assert `fast==slow` and `fast>slow` combos are dropped and the drop
  count is logged.
- Existing backtest tests must stay green (new fields additive; `compute_metrics` empty
  case returns the new keys with 0.0/None).

## Open calibration (resolved during implementation, not blocking)

- M (sample size) and W (window count): pick M from a measured GDU6 per-backtest time to
  fit overnight; W default 4, revisit if a contract's span is short.
