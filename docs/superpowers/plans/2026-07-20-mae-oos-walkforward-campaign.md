# MAE + OOS/walk-forward + fast<slow + live GDU6 re-run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score backtests by their true open-position risk (MAE + mark-to-market drawdown), validate configs out-of-sample, stop sweeping degenerate macd configs, and re-run the GDU6 macd_cross campaign as random-sampled configs feeding a live, deploy-from-mid-run hit-parade.

**Architecture:** Add per-bar unrealized tracking to the existing backtest loop; a pure `window_metrics` helper slices IS/OOS + W windows from the collected trades; a one-line sampler predicate drops `fast>=slow`; the campaign uses the existing random-`paramSets` + `camp-`-mirroring path with the new metrics surfaced as leaderboard columns.

**Tech Stack:** Python 3.12 (poetry, pytest), the `trader/lab` backtester (`BacktestRuntime`), `scripts/queue_campaign.py`, Postgres `optimization_leaderboard`, Svelte 5 Botstore leaderboard.

## Global Constraints

- Unit tests need no credentials: `poetry run pytest -m "not integration" -q`.
- No non-ASCII in hot-path console lines (RU-Windows cp1251); this plan is backtest-side (STL), lower risk, but keep log lines ASCII.
- New result fields are ADDITIVE — every existing backtest result/test must stay green.
- `recovery_factor` field name is unchanged (back-compat); only its UI LABEL becomes "closed-trade RF".
- MAE / drawdown returned by the engine are RUB positive magnitudes; the UI derives % of margin.
- Ranking metric = `recovery_factor_mtm_OOS`; guardrails shown = `max_mae`, `windows_profitable/W`.
- Campaign symbol = GDU6, strategy = macd_cross, `--pin qty=1`. Sample size M sized to finish overnight (< ~8h i9 wall), measured before the run.

---

### Task 1: `window_metrics` pure helper (IS/OOS split + W-window consistency)

**Files:**
- Create: `trader/lab/window_metrics.py`
- Test: `tests/lab/test_window_metrics.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `window_metrics(pairs: list[dict], span: tuple[float, float], is_frac: float = 0.7, splits: int = 4) -> dict` where each `pair` is `{"time": float, "pnl": float}` (a closed round-trip's exit time + net RUB), `span` is `(first_bar_time, last_bar_time)`. Returns `{"net_is": float, "net_oos": float, "degrade": float | None, "windows_profitable": int, "windows_total": int}`. `degrade = oos_rate / is_rate` where each rate = net / window_seconds (None if is_rate <= 0).

- [ ] **Step 1: Write the failing tests**

```python
# tests/lab/test_window_metrics.py
from trader.lab.window_metrics import window_metrics


def _pairs(*items):
    return [{"time": t, "pnl": p} for t, p in items]


def test_is_oos_split_by_time_70_30():
    # span 0..100; is boundary at 70. pairs at t=10 (+100 IS), t=80 (+30 OOS).
    m = window_metrics(_pairs((10, 100.0), (80, 30.0)), span=(0.0, 100.0), is_frac=0.7, splits=4)
    assert m["net_is"] == 100.0
    assert m["net_oos"] == 30.0
    # is_rate = 100/70, oos_rate = 30/30 -> degrade = (30/30)/(100/70) = 0.7
    assert m["degrade"] == round((30 / 30) / (100 / 70), 6)


def test_window_consistency_counts_profitable_windows():
    # span 0..100, 4 windows: [0,25) [25,50) [50,75) [75,100].
    # profits in windows 0 and 2, loss in window 1, nothing in window 3.
    m = window_metrics(_pairs((10, 50.0), (30, -20.0), (60, 40.0)),
                       span=(0.0, 100.0), is_frac=0.7, splits=4)
    assert m["windows_total"] == 4
    assert m["windows_profitable"] == 2


def test_degrade_none_when_is_flat():
    m = window_metrics(_pairs((80, 30.0)), span=(0.0, 100.0), is_frac=0.7, splits=4)
    assert m["net_is"] == 0.0
    assert m["degrade"] is None


def test_empty_pairs():
    m = window_metrics([], span=(0.0, 100.0))
    assert m == {"net_is": 0.0, "net_oos": 0.0, "degrade": None,
                 "windows_profitable": 0, "windows_total": 4}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/lab/test_window_metrics.py -q`
Expected: FAIL with `ModuleNotFoundError: trader.lab.window_metrics`.

- [ ] **Step 3: Write minimal implementation**

```python
# trader/lab/window_metrics.py
"""Time-sliced backtest scoring: an in-sample/out-of-sample split and per-window
consistency, computed from a run's CLOSED round-trips. Pure — no I/O, no clock —
so it is unit-tested without running a backtest. A config that only prints in one
window (the GDU6 curve-fit) scores low on windows_profitable and degrade."""
from __future__ import annotations


def window_metrics(pairs: list[dict], span: tuple[float, float],
                   is_frac: float = 0.7, splits: int = 4) -> dict:
    t0, t1 = span
    total = t1 - t0
    if total <= 0:
        return {"net_is": 0.0, "net_oos": 0.0, "degrade": None,
                "windows_profitable": 0, "windows_total": splits}

    boundary = t0 + total * is_frac
    net_is = sum(p["pnl"] for p in pairs if p["time"] < boundary)
    net_oos = sum(p["pnl"] for p in pairs if p["time"] >= boundary)

    is_secs = total * is_frac
    oos_secs = total * (1.0 - is_frac)
    is_rate = (net_is / is_secs) if is_secs > 0 else 0.0
    oos_rate = (net_oos / oos_secs) if oos_secs > 0 else 0.0
    degrade = round(oos_rate / is_rate, 6) if is_rate > 0 else None

    win = total / splits
    sums = [0.0] * splits
    for p in pairs:
        idx = min(splits - 1, int((p["time"] - t0) / win))
        sums[idx] += p["pnl"]
    windows_profitable = sum(1 for s in sums if s > 0)

    return {"net_is": net_is, "net_oos": net_oos, "degrade": degrade,
            "windows_profitable": windows_profitable, "windows_total": splits}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/lab/test_window_metrics.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add trader/lab/window_metrics.py tests/lab/test_window_metrics.py
git commit -m "feat(lab): window_metrics — IS/OOS split + per-window consistency"
```

---

### Task 2: MAE + mark-to-market drawdown in the backtest loop

**Files:**
- Modify: `trader/lab/backtest.py` — `compute_metrics` empty dict (~L62-65) + `run_one_backtest` bar loop (~L191-213)
- Test: `tests/lab/test_backtest_mae.py`

**Interfaces:**
- Consumes: `BacktestRuntime._positions[symbol]` = `{"side": "long"|"short"|"flat", "qty": int, "avg": float}`, `runtime._equity` (realized RUB), `runtime._point_value`, `bar.close` (all already present).
- Produces: `run_one_backtest`'s result dict gains `max_mae: float`, `max_drawdown_mtm: float`, `recovery_factor_mtm: float | None` (all RUB positive magnitudes; RF None when dd==0). `compute_metrics`'s `empty` dict gains the same three keys defaulted.

- [ ] **Step 1: Write the failing test**

```python
# tests/lab/test_backtest_mae.py
import asyncio
from trader.lab.runtime import Bar
from trader.lab import backtest


# A strategy that longs 1 on the first bar and holds — so the open position rides
# the full price path. Price dips hard (unrealized hole) then recovers to a small
# gain at the last bar's close. The mirage: closed-trade drawdown ~0, MAE large.
async def on_bar(rt, params):
    if rt._cursor == 0 and rt.signed_position(rt._symbol) == 0:
        await rt.buy(rt._symbol, 1, rt.bars[0].close)


def _bar(t, price):
    return Bar(time=t, open=price, high=price, low=price, close=price, volume=1)


def test_mae_and_mtm_drawdown_expose_open_position_risk():
    # close: 100 -> 60 (deep unrealized hole) -> 105 (small gain, still OPEN)
    bars = [_bar(0, 100.0), _bar(60, 60.0), _bar(120, 105.0)]
    res = asyncio.run(backtest.run_one_backtest(
        strategy_module=__import__(__name__, fromlist=["on_bar"]),
        params={}, bars=bars, symbol="TEST", initial_equity=100_000.0,
        point_value=1.0))
    # No round-trip closed -> closed-trade drawdown is ~0 (the mirage).
    assert res["max_drawdown"] <= 1.0
    # But MAE saw the 40-point hole on 1 contract = 40 RUB.
    assert abs(res["max_mae"] - 40.0) < 1e-6
    assert abs(res["max_drawdown_mtm"] - 40.0) < 1e-6
    assert res["recovery_factor_mtm"] is not None
```

Note: match the real accessor names — if `BacktestRuntime` exposes position via a
method other than `signed_position(symbol)` / a `buy(symbol, qty, price)` coroutine,
adapt the test's strategy helper to the actual API (read `trader/lab/runtime.py`
first). The ASSERTIONS on `max_mae`/`max_drawdown_mtm`/`max_drawdown` are the contract.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/lab/test_backtest_mae.py -q`
Expected: FAIL — `KeyError: 'max_mae'`.

- [ ] **Step 3: Implement — extend the empty dict and the loop**

In `compute_metrics`, extend the `empty` dict (~L62-65) to include the three keys:

```python
    empty = {"total_trades": 0, "win_rate": 0.0, "total_return": 0.0,
             "sharpe": None, "max_drawdown": 0.0, "recovery_factor": None,
             "net_profit": 0.0, "peak_contracts": 0, "margin_used": 0.0,
             "ann_return_go": None, "ann_return_full": None,
             "max_mae": 0.0, "max_drawdown_mtm": 0.0, "recovery_factor_mtm": None}
```

In `run_one_backtest`, replace the bar loop + return (~L191-213):

```python
    equity_curve = []
    mtm_peak = runtime._equity          # highest mark-to-market equity seen
    max_dd_mtm = 0.0                    # deepest peak->trough on the mtm curve
    max_mae = 0.0                       # worst open-position adverse excursion (RUB)
    while True:
        await strategy_module.on_bar(runtime, params)
        bar = bars[runtime._cursor]
        pos = runtime._positions.get(symbol, {"side": "flat", "qty": 0, "avg": 0.0})
        signed = pos["qty"] * (1 if pos["side"] == "long"
                               else -1 if pos["side"] == "short" else 0)
        unreal = signed * (bar.close - pos["avg"]) * point_value if signed else 0.0
        mtm = runtime._equity + unreal
        mtm_peak = max(mtm_peak, mtm)
        max_dd_mtm = max(max_dd_mtm, mtm_peak - mtm)
        if unreal < 0:
            max_mae = max(max_mae, -unreal)
        equity_curve.append({"time": bar.time, "equity": runtime._equity})
        if not runtime.advance():
            break

    if hasattr(strategy_module, "on_stop"):
        await strategy_module.on_stop(runtime, params)

    trades = [
        {"side": o.side, "price": o.fill_price or o.price, "qty": o.qty, "time": o.fill_time}
        for o in await runtime.get_orders()
    ]
    bars_days = (bars[-1].time - bars[0].time) / 86400.0 if len(bars) > 1 else 0.0
    metrics = compute_metrics(trades, initial_equity, point_value, symbol=symbol,
                              initial_margin=initial_margin, bars_days=bars_days)
    rf_mtm = (metrics["net_profit"] / max_dd_mtm) if max_dd_mtm > 0 else None
    return {"trades": trades, "equity_curve": equity_curve,
            "point_value": point_value,
            "max_mae": max_mae, "max_drawdown_mtm": max_dd_mtm,
            "recovery_factor_mtm": rf_mtm, **metrics}
```

(`**metrics` is spread LAST so `compute_metrics`'s own keys win for shared names; the
three new keys are set before it and are not in `metrics`, so they survive.)

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/lab/test_backtest_mae.py -q`
Expected: PASS. Then `poetry run pytest tests/lab -q` — all green (new keys additive).

- [ ] **Step 5: Commit**

```bash
git add trader/lab/backtest.py tests/lab/test_backtest_mae.py
git commit -m "feat(lab): MAE + mark-to-market drawdown expose open-position risk (RF mirage fix)"
```

---

### Task 3: Wire window_metrics into the backtest result

**Files:**
- Modify: `trader/lab/backtest.py` — `run_one_backtest` (after `compute_metrics`)
- Test: `tests/lab/test_backtest_mae.py` (add a case)

**Interfaces:**
- Consumes: `window_metrics` (Task 1); the `trades` list already built.
- Produces: result dict gains `net_oos`, `recovery_factor_mtm_oos`, `degrade`, `windows_profitable`, `windows_total`. `recovery_factor_mtm_oos` reuses the OOS pairs' net over the OOS slice of the mtm drawdown — for v1 use `net_oos / max_drawdown_mtm` (whole-run dd; a per-slice dd is a later refinement, note it).

- [ ] **Step 1: Add failing assertions**

```python
# append to tests/lab/test_backtest_mae.py
def test_result_carries_window_metrics():
    bars = [_bar(0, 100.0), _bar(60, 60.0), _bar(120, 105.0)]
    res = asyncio.run(backtest.run_one_backtest(
        strategy_module=__import__(__name__, fromlist=["on_bar"]),
        params={}, bars=bars, symbol="TEST", initial_equity=100_000.0,
        point_value=1.0))
    for k in ("net_oos", "recovery_factor_mtm_oos", "degrade",
              "windows_profitable", "windows_total"):
        assert k in res
    assert res["windows_total"] == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/lab/test_backtest_mae.py::test_result_carries_window_metrics -q`
Expected: FAIL — `KeyError: 'net_oos'`.

- [ ] **Step 3: Implement — build closed-pair pnl+time, call window_metrics**

Add to `run_one_backtest` before the final return, and merge into it. Reuse the same
signed-space close accounting `compute_metrics` uses, but keyed by exit TIME:

```python
    from trader.lab.window_metrics import window_metrics
    # Closed round-trips as (exit_time, net_pnl_RUB) for time-sliced scoring.
    pairs, _signed, _avg = [], 0, 0.0
    for t in trades:
        q = t["qty"]; px = t["price"]; delta = q if t["side"] == "buy" else -q
        if _signed != 0 and (_signed > 0) != (delta > 0):
            closed = min(q, abs(_signed))
            pnl = ((px - _avg) if _signed > 0 else (_avg - px)) * closed * point_value
            pairs.append({"time": t["time"], "pnl": pnl})
        new_signed = _signed + delta
        if new_signed == 0:
            _avg = 0.0
        elif _signed != 0 and (_signed > 0) == (delta > 0):
            _avg = (_avg * abs(_signed) + px * q) / (abs(_signed) + q)
        elif _signed != 0 and (new_signed > 0) == (_signed > 0):
            pass  # partial reduce keeps avg
        else:
            _avg = px
        _signed = new_signed
    span = (bars[0].time, bars[-1].time)
    wm = window_metrics(pairs, span=span, is_frac=0.7, splits=4)
    rf_mtm_oos = (wm["net_oos"] / max_dd_mtm) if max_dd_mtm > 0 else None
```

Then extend the return dict with:
`"net_oos": wm["net_oos"], "recovery_factor_mtm_oos": rf_mtm_oos, "degrade": wm["degrade"], "windows_profitable": wm["windows_profitable"], "windows_total": wm["windows_total"],`

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/lab/test_backtest_mae.py -q`
Expected: all pass. Then `poetry run pytest tests/lab -q`.

- [ ] **Step 5: Commit**

```bash
git add trader/lab/backtest.py tests/lab/test_backtest_mae.py
git commit -m "feat(lab): slice IS/OOS + window consistency into the backtest result"
```

---

### Task 4: fast<slow filter in the sampler

**Files:**
- Modify: `scripts/queue_campaign.py` — the sample/grid path (`_build_grid` ~L72 and the random-`paramSets` builder)
- Test: `tests/lab/test_queue_campaign_filter.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `valid_macd_config(cfg: dict) -> bool` returning False when `cfg.get("fast")` and `cfg.get("slow")` are both present and `fast >= slow`. Applied to every sampled/grid config for macd_cross; the number DROPPED is `log()`-ed (no silent truncation).

- [ ] **Step 1: Write the failing test**

```python
# tests/lab/test_queue_campaign_filter.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "queue_campaign", pathlib.Path("scripts/queue_campaign.py"))
qc = importlib.util.module_from_spec(spec); spec.loader.exec_module(qc)


def test_rejects_fast_ge_slow():
    assert qc.valid_macd_config({"fast": 12, "slow": 26}) is True
    assert qc.valid_macd_config({"fast": 26, "slow": 26}) is False   # fast==slow
    assert qc.valid_macd_config({"fast": 30, "slow": 26}) is False   # inverted
    assert qc.valid_macd_config({"signal": 9}) is True               # not a macd config
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/lab/test_queue_campaign_filter.py -q`
Expected: FAIL — `AttributeError: valid_macd_config`.

- [ ] **Step 3: Implement the predicate + apply it**

```python
# scripts/queue_campaign.py (module level)
def valid_macd_config(cfg: dict) -> bool:
    """A macd config with fast>=slow is degenerate (fast==slow -> zero MACD line;
    fast>slow -> inverted). Non-macd configs (no fast/slow) always pass."""
    f, s = cfg.get("fast"), cfg.get("slow")
    if f is None or s is None:
        return True
    return f < s
```

Apply where configs are materialised for engine=remote (the `paramSets` list / grid
product): filter with `valid_macd_config`, and log the drop count, e.g.:

```python
    before = len(param_sets)
    param_sets = [c for c in param_sets if valid_macd_config(c)]
    dropped = before - len(param_sets)
    if dropped:
        print(f"[campaign] dropped {dropped} degenerate fast>=slow macd configs")
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/lab/test_queue_campaign_filter.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/queue_campaign.py tests/lab/test_queue_campaign_filter.py
git commit -m "feat(campaign): drop degenerate fast>=slow macd configs before queueing"
```

---

### Task 5: Surface new metrics in the leaderboard + deploy-from-row

**Files:**
- Modify: `frontend/src/components/lab/Botstore.svelte` (leaderboard table — the `optimization_leaderboard` render)
- Modify (if the mirror stores explicit columns rather than a metrics JSON): the leaderboard mirror writer (grep `optimization_leaderboard` under `trader/`) to persist `max_mae`, `recovery_factor_mtm_oos`, `windows_profitable`, `degrade`. If results are stored as a metrics JSON blob, they ride along automatically — verify first.

**Interfaces:**
- Consumes: result fields from Tasks 2-3.
- Produces: leaderboard columns `RF_mtm_OOS` (sort key), `MAE ₽`, `окна N/W`, `degrade%`; the existing per-row deploy/clone-to-paper action reused so a row can be launched mid-run.

- [ ] **Step 1: Verify how leaderboard metrics are stored**

Run: `grep -rn "optimization_leaderboard" trader/ | head`. Confirm whether metrics are a
JSON column (fields ride free) or explicit columns (need to add `max_mae` etc. to the
INSERT + a migration). Do the DB-side change ONLY if columns are explicit.

- [ ] **Step 2: Add the columns to the leaderboard table (frontend)**

In `Botstore.svelte`, add header cells + row cells for `recovery_factor_mtm_oos`
(as "RF (OOS)", the default sort), `max_mae` (as "MAE ₽"), `windows_profitable`/`windows_total`
(as "окна N/4"), `degrade` (as "degrade%", ×100). Keep font >=10px; reuse the existing
number-cell classes. Make `RF (OOS)` the default sort column (replace the old
`recovery_factor` sort) and relabel the old one "closed-trade RF".

- [ ] **Step 3: Reuse the deploy-from-row action**

Confirm the leaderboard row already has a launch/clone-to-paper control (Botstore had a
launch modal, `nameFor()`/launch path). If present, no change. If a leaderboard row lacks
it, wire the row's params into the existing clone-to-paper deploy call so a config is
deployable while the campaign still runs.

- [ ] **Step 4: Build + deploy the frontend**

Build via PowerShell (bash build gets classifier-blocked):
`Set-Location "…/frontend"; node ./node_modules/vite/bin/vite.js build`
Then deploy per CLAUDE.md SAFE deploy: `git push` -> `ssh hoster 'git pull'` -> scp
`dist/index.html` + the new hashed `dist/assets/index-*.js|.css`. No service restart.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/lab/Botstore.svelte
git commit -m "feat(lab): leaderboard shows RF(OOS)/MAE/window-consistency + deploy from row"
```

---

### Task 6: Calibrate M and run the GDU6 campaign

**Files:**
- Use: `scripts/queue_campaign.py` (no code change — invocation only)

- [ ] **Step 1: Measure one GDU6 macd_cross backtest wall time**

Queue a tiny 1-config `paramSets` GDU6 run, note the i9 per-backtest seconds from the
pull-agent heartbeat / `backtest_results` timing. Compute `M = floor(overnight_seconds /
per_backtest_seconds * i9_cores)` targeting < ~8h wall. Record the number in the run log.

- [ ] **Step 2: Ensure the i9 will actually claim (not idle)**

Check `agent_control.pause_remote != '1'` (else claim returns 204 forever) and stop
`shectory-optimizer.service` on the hoster for a focused sweep (it competes for the i9).
See CLAUDE.md "Param sweeps".

- [ ] **Step 3: Queue the campaign**

```bash
python scripts/queue_campaign.py --strategies macd_cross --symbols GDU6 \
  --include-avg-params --pin qty=1 --sample M --run-id-prefix camp-
```
(random no-repeat sample of M configs, fast<slow already enforced by Task 4).

- [ ] **Step 4: Watch the live hit-parade**

Confirm rows appear in the Botstore leaderboard as configs finish (ranked by RF(OOS),
MAE + window-consistency visible), and that a row can be deployed to paper mid-run.

- [ ] **Step 5: Restore the optimizer service**

Re-start `shectory-optimizer.service` after the focused sweep. Record run-id + top configs.

---

## Self-Review

- **Spec coverage:** MAE + mtm drawdown (Task 2), OOS/window (Tasks 1,3), fast<slow (Task 4),
  live leaderboard + deploy (Task 5), GDU6 re-run + overnight calibration (Task 6). All spec
  sections covered.
- **Types:** `window_metrics(pairs, span, is_frac, splits)` used identically in Tasks 1 and 3;
  result keys `max_mae`/`max_drawdown_mtm`/`recovery_factor_mtm`/`net_oos`/
  `recovery_factor_mtm_oos`/`degrade`/`windows_profitable`/`windows_total` consistent across
  Tasks 2,3,5.
- **Open items:** the real `BacktestRuntime` strategy API (`buy`/`signed_position` names) is
  verified in Task 2 Step 1 by reading `runtime.py` first — the assertions are the contract.
  `recovery_factor_mtm_oos` uses whole-run `max_dd_mtm` in v1 (a per-OOS-slice dd is a noted
  later refinement, not blocking).
