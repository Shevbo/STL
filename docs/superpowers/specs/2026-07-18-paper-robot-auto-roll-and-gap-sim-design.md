# Paper-robot auto-roll + gap simulation

Date: 2026-07-18
Status: approved (design)

## Problem

STL Lab paper robots (the витрина "РОБОТЫ" bottom table) carry a HARDCODED specific
contract in `params_json.symbol` (e.g. `RIM6`). When that contract expires, ISS stops
returning fresh bars, the strategy sees no new bar, the robot idles, and its statistics
freeze. Many showcase robots died at the June/July expiries — no continuous stats.

The robots are PAPER simulations for comparison (not real money; real trading runs on the
QUIK agent). We want them to keep producing continuous comparison statistics across the
contract roll instead of freezing.

## Goals

1. **Seamless quote stream** — a paper robot on a specific contract automatically follows
   the front contract of its base series (RIM6 → RIU6 when June expires). The feed never
   dies at expiry.
2. **Gap simulation** — for the dead window (old-contract expiry → now), simulate the
   strategy on the new front contract and record the fills so the statistics are continuous
   (no empty hole).

## Decisions (confirmed with operator)

- **Roll behavior:** at roll, robot is FLAT on the new contract; strategy in-memory state
  is reset (indicators recompute from scratch, brief warmup). No position/avg carried across
  the roll → no phantom P&L jump. Matches the existing per-contract P&L model and the manual
  roll procedure (DB update + state reset).
- **Simulation window:** from the OLD contract's expiry (LastDel) to today.
- **Storage of simulated fills:** `live_trades` rows with `status='paper'` (so they blend
  into the shown P&L, as requested) and `order_id` prefixed `sim-` (auditable + reversible
  with one `DELETE ... WHERE order_id LIKE 'sim-%'`).
- **Auto-roll scope:** ON by default for ALL paper robots. Real (`live_real`) STL robots are
  NEVER auto-rolled (arming real money is human-initiated). Base-code symbols (`RI`) are left
  alone — they already roll via continuous bars.

## Architecture

Three units, each independently testable.

### Unit 1 — front-contract resolver: `trader/lab/contract_roll.py` (new)

- `base_of(symbol: str) -> str | None` — strip the trailing month-letter+year-digit from a
  specific contract (`RIM6` → `RI`, `BRN6` → `BR`, `SiU6` → `Si`). Returns None if the
  symbol is not a specific contract (`is_specific_contract` from iss_loader gates this).
- `async def front_contract(symbol: str) -> str | None` — resolve the base's current front
  contract via ISS: enumerate the base's securities, pick the one whose `LastDelDate >= today`
  with the nearest LastDel (the live front). Reuses `IssLoader` / the LastDel enumeration
  already in `iss_loader.fetch_continuous_bars` / `top_instruments`. Returns None on ISS
  failure (caller keeps the old symbol).
- Result cached per base, TTL ~6h (front changes quarterly/monthly; don't hit ISS per tick).

What it depends on: `iss_loader` (ISS session + LastDel enumeration). Pure resolver, no DB.

### Unit 2 — auto-roll hook in the scheduler: `trader/lab/scheduler.py`

In `_run_robot_task`, before compiling/running `on_bar`:
- Skip if the robot is real (`state_json.live_real` true) or the symbol is not a specific
  contract.
- `front = await front_contract(params.symbol)`. If `front` is None or equals the current
  symbol → no-op.
- Otherwise ROLL:
  - Mutate the long-lived in-memory `robot.params_json["symbol"] = front` (the `robot`
    object lives for the whole `_window_loop`; without mutating it, the next tick re-reads
    the old symbol and rolls forever).
  - `UPDATE robots SET params_json=$1, state_json=$2 WHERE id=$3` — persist the new symbol
    and the reset state.
  - State reset = drop ALL strategy keys, PRESERVE only `live_real` (paper/real flag):
    `new_state = {"live_real": old_state.get("live_real", False)}`. Set both
    `self._robot_states[robot.id]` and the persisted `state_json` to it, so indicators and
    the paper position restart flat. `self._compiled` is untouched (script unchanged).
  - `log.info("lab.roll", robot_id, old, new)`.
- The paper position is naturally flat on the new symbol (`_paper_position` scopes fills by
  symbol; the new symbol has no fills yet). Old-contract fills stay in `live_trades` (history).

What it depends on: Unit 1, the DB pool, existing runtime. Guarded so an ISS hiccup never
rolls to an empty symbol.

### Unit 3 — gap simulation: `scripts/backfill_roll_sim.py` (new, one-shot)

Runs standalone on the hoster (NOT in the API process — heavy-compute isolation rule).
Connects to the DB directly (asyncpg + the app's DSN), for each PAPER robot whose
`params.symbol` is a specific contract that is no longer the front:
- `old = params.symbol`; `new = front_contract(old)`; skip if new is None or == old.
- Window: `date_from = LastDel(old)` (via `fetch_contract_spec`/ISS) or the robot's last
  recorded trade time, whichever is later; `date_to = today`.
- `bars = load_bars_iss(new, date_from, date_to, interval=1)`.
- Run the strategy through `BacktestRuntime(bars, new, ...)` — same `script_code` + `params`
  (with `symbol=new`) the scheduler uses, resolved via the same compile path.
- Collect `runtime._orders` (fills). For each: insert a `live_trades` row
  `(symbol=new, side, qty, price=fill_price, order_id='sim-'+hex, status='paper',
  timestamp=fill_time)`.
- Bulk insert; then `UPDATE robots SET params_json.symbol = new`, reset state (so the live
  scheduler auto-roll in Unit 2 continues forward from `new`).
- Idempotent: delete existing `sim-` rows for the robot on the new symbol before reinserting,
  so re-running the backfill does not double-count.

What it depends on: Unit 1, `iss_loader`, `BacktestRuntime`, DB. Reversible via the `sim-`
prefix.

## Data flow

```
expiry freezes robot
      │
Unit 3 (one-shot): backtest new contract over the gap → live_trades (status=paper, sim-)
      │                                          + params.symbol := front, state reset
      ▼
Unit 2 (ongoing, per tick): front(base) != symbol ? → roll (params.symbol := front, flat) → on_bar trades new contract
      ▼
showcase / RobotWindow / LiveRobots recompute P&L from live_trades PER CONTRACT (unchanged)
```

The P&L layer is untouched: it already computes per contract from `live_trades` fills and
never cross-pairs across the roll. Simulated fills are ordinary `paper` fills to it.

## Error handling

- ISS failure in `front_contract` → return None → caller keeps the old symbol, retries next
  tick / next run. Never roll to an empty symbol.
- Backfill: a robot whose new-contract bars are empty is skipped with a logged reason (no
  silent no-op).
- Auto-roll never touches real robots (guard on `live_real`).

## Testing

- `tests/lab/test_contract_roll.py`:
  - `base_of`: RIM6→RI, BRN6→BR, SiU6→Si, GZU6→GZ; base code / junk → None.
  - `front_contract`: given a mocked ISS security set with LastDel dates, picks the nearest
    not-yet-expired contract; returns None when ISS returns nothing.
  - roll decision: front == symbol → no roll; front != symbol → roll payload (new symbol,
    state cleared).
- Backfill self-check (`__main__` / a small test): a synthetic 3-fill BacktestRuntime run
  maps to 3 `live_trades` rows with `sim-` order_ids and correct symbol/timestamps.

## Out of scope (YAGNI)

- UI button to trigger the backfill (one-shot script suffices; Unit 2 keeps the future
  covered). Add later if the operator wants on-demand re-sim.
- Synthetic back-adjusted continuous price stream for live paper (per-contract P&L is already
  correct; splicing prices would invent phantom profit).
- Auto-roll for real STL robots (human-initiated by policy).
