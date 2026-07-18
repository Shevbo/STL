# Paper-robot auto-roll + gap simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paper Lab robots follow the front futures contract across expiry (seamless quote stream), and the dead window since expiry is backfilled by simulating the strategy on the new contract so the витрина statistics are continuous.

**Architecture:** Three isolated units. (1) A front-contract resolver maps a specific contract to its live front via ISS (cached 6h). (2) The scheduler rolls each paper robot to the front before every tick — new symbol, flat, fresh state. (3) A one-shot script backtests the gap on the new contract and writes the fills into `live_trades` as `paper`/`sim-` rows.

**Tech Stack:** Python 3.12, asyncio, asyncpg, httpx, MOEX ISS (free). Existing `trader/lab/iss_loader.py`, `trader/lab/backtest.py`, `trader/lab/runtime.py`, `trader/lab/scheduler.py`.

## Global Constraints

- Auto-roll applies to PAPER robots only; NEVER auto-roll a robot whose `state_json.live_real` is true (arming real money is human-initiated).
- Roll = flat + fresh state: preserve only `live_real`, drop all strategy state keys. No position/avg carried across the roll (no phantom P&L).
- Simulated fills: `live_trades.status='paper'`, `order_id` prefixed `sim-`; reversible with `DELETE FROM live_trades WHERE order_id LIKE 'sim-%'`.
- Never roll to an empty/None symbol (ISS failure) — keep the old symbol, retry.
- Heavy compute (the backfill backtests) runs OUTSIDE the STL API process — a standalone script on the hoster, not an endpoint.
- Simulated-fill timestamp = `bar.time - 3*3600` stored as naive UTC (ISS bars are MSK-wall stamped as UTC; `live_trades.timestamp` is true UTC — the chart shifts +3h back onto the bar grid).
- Base code extraction: strip the trailing `[month-letter][year-digit]` (RIM6→RI, BRN6→BR, SiU6→Si). Reuse `iss_loader.is_specific_contract`.

---

### Task 1: Front-contract resolver

**Files:**
- Create: `trader/lab/contract_roll.py`
- Test: `tests/lab/test_contract_roll.py`

**Interfaces:**
- Consumes: `trader.lab.iss_loader.is_specific_contract(symbol: str) -> bool`
- Produces:
  - `base_of(symbol: str) -> str | None`
  - `async front_contract(symbol: str, today: date | None = None) -> str | None`
  - `fills_to_rows(robot_id: str, symbol: str, fills: list[dict]) -> list[tuple]` (pure sim-fill → live_trades row mapper — lives in the package so the CLI script stays thin and it is unit-testable without importing `scripts`)

- [ ] **Step 1: Write the failing test**

```python
# tests/lab/test_contract_roll.py
from datetime import date
import pytest
from trader.lab import contract_roll as cr


def test_base_of():
    assert cr.base_of("RIM6") == "RI"
    assert cr.base_of("BRN6") == "BR"
    assert cr.base_of("SiU6") == "Si"
    assert cr.base_of("GZU6") == "GZ"
    assert cr.base_of("RI") is None        # base code, not a specific contract
    assert cr.base_of("") is None


@pytest.mark.asyncio
async def test_front_contract_picks_nearest_future(monkeypatch):
    # Mocked ISS security table: (SECID, LASTTRADEDATE)
    rows = [
        ("RIM6", "2026-06-18"),   # expired
        ("RIU6", "2026-09-17"),   # front (nearest future)
        ("RIZ6", "2026-12-17"),   # back month
        ("BRU6", "2026-08-31"),   # different series
    ]
    async def fake_secs():
        return rows
    monkeypatch.setattr(cr, "_securities", fake_secs)
    got = await cr.front_contract("RIM6", today=date(2026, 7, 18))
    assert got == "RIU6"


@pytest.mark.asyncio
async def test_front_contract_none_when_no_future(monkeypatch):
    async def fake_secs():
        return [("RIM6", "2026-06-18")]   # only expired
    monkeypatch.setattr(cr, "_securities", fake_secs)
    assert await cr.front_contract("RIM6", today=date(2026, 7, 18)) is None
    # non-specific symbol → None regardless
    assert await cr.front_contract("RI", today=date(2026, 7, 18)) is None


def test_fills_to_rows_maps_and_marks_sim():
    from datetime import datetime
    fills = [
        {"side": "buy",  "price": 88000.0, "qty": 1, "time": 1_760_000_000},
        {"side": "sell", "price": 88100.0, "qty": 1, "time": 1_760_000_060},
    ]
    rows = cr.fills_to_rows("robot-x", "RIU6", fills)
    assert len(rows) == 2
    # tuple order: (id, robot_id, symbol, side, qty, price, order_id, status, ts)
    r0 = rows[0]
    assert r0[1] == "robot-x" and r0[2] == "RIU6" and r0[3] == "buy" and r0[4] == 1
    assert float(r0[5]) == 88000.0
    assert r0[6].startswith("sim-")
    assert r0[7] == "paper"
    # ISS bar time (MSK-wall-as-UTC) shifted -3h to true UTC, naive
    assert r0[8] == datetime.utcfromtimestamp(1_760_000_000 - 3 * 3600)
    assert r0[8].tzinfo is None
    assert rows[0][6] != rows[1][6]          # unique sim ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/lab/test_contract_roll.py -v`
Expected: FAIL with `ModuleNotFoundError: trader.lab.contract_roll` (or `AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# trader/lab/contract_roll.py
"""Front-contract resolver + roll helper for paper Lab robots.

A paper robot's params.symbol is a specific contract (e.g. RIM6). When it expires
the ISS feed dies and the robot freezes. front_contract() maps the symbol's series
to today's live front contract (RIM6 -> RIU6) so the scheduler can roll it.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx

from trader.lab.iss_loader import is_specific_contract

_SECURITIES_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
    "?iss.meta=off&iss.only=securities"
    "&securities.columns=SECID,LASTTRADEDATE"
)
_CACHE_TTL = 6 * 3600.0  # front changes quarterly/monthly — one ISS call per 6h
_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def base_of(symbol: str) -> str | None:
    """Series base of a specific contract: RIM6->RI, BRN6->BR, SiU6->Si.
    None if `symbol` is not a specific FORTS contract."""
    if not symbol or not is_specific_contract(symbol):
        return None
    return symbol[:-2]


async def _securities() -> list[tuple[str, str]]:
    """(SECID, LASTTRADEDATE) for all FORTS securities, cached 6h."""
    now = _time.monotonic()
    hit = _cache.get("all")
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "STL/1.0"}) as c:
        j = (await c.get(_SECURITIES_URL)).json()
    sec = j.get("securities", {})
    cols = sec.get("columns", [])
    rows: list[tuple[str, str]] = []
    for row in sec.get("data", []):
        d = dict(zip(cols, row))
        sid, ltd = d.get("SECID"), d.get("LASTTRADEDATE")
        if sid and ltd:
            rows.append((sid, ltd))
    if rows:
        _cache["all"] = (now, rows)
    return rows


async def front_contract(symbol: str, today: date | None = None) -> str | None:
    """Today's live front contract for `symbol`'s series (RIM6 -> RIU6).
    None if `symbol` isn't a specific contract or ISS returned nothing usable."""
    base = base_of(symbol)
    if base is None:
        return None
    today = today or date.today()
    try:
        rows = await _securities()
    except Exception:
        return None
    cands: list[tuple[str, date]] = []
    for sid, ltd in rows:
        if base_of(sid) != base:
            continue
        try:
            d = datetime.strptime(ltd, "%Y-%m-%d").date()
        except Exception:
            continue
        if d >= today:
            cands.append((sid, d))
    if not cands:
        return None
    return min(cands, key=lambda x: x[1])[0]


def fills_to_rows(robot_id: str, symbol: str, fills: list[dict]) -> list[tuple]:
    """Map backtest fills -> live_trades INSERT tuples for the gap simulation.
    ISS bar time is MSK-wall stamped as UTC; live_trades.timestamp is true UTC, so
    shift -3h and store naive. Column order matches the INSERT in the backfill script.
    fills: list of {side, price, qty, time} from backtest.run_single_backtest."""
    rows: list[tuple] = []
    for f in fills:
        ts = datetime.utcfromtimestamp(int(f["time"]) - 3 * 3600)
        rows.append((
            uuid4().hex, robot_id, symbol, f["side"], int(f["qty"]),
            Decimal(str(f["price"])), "sim-" + uuid4().hex[:10], "paper", ts,
        ))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/lab/test_contract_roll.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add trader/lab/contract_roll.py tests/lab/test_contract_roll.py
git commit -m "feat(lab): front-contract resolver for paper-robot roll"
```

---

### Task 2: Auto-roll hook in the scheduler

**Files:**
- Modify: `trader/lab/scheduler.py` (add `_maybe_roll`, call it at the top of `_run_robot_task`)
- Test: `tests/lab/test_scheduler_roll.py`

**Interfaces:**
- Consumes: `contract_roll.front_contract`, `contract_roll.base_of` (Task 1)
- Produces: `RobotScheduler._maybe_roll(self, robot) -> None` (mutates `robot.params_json`, `robot.state_json`, `self._robot_states[robot.id]`, and persists to DB when `self._pool` is set)

- [ ] **Step 1: Write the failing test**

```python
# tests/lab/test_scheduler_roll.py
import types
import pytest
from trader.lab.scheduler import RobotScheduler
from trader.lab import contract_roll as cr


def _robot(symbol, live_real=False):
    return types.SimpleNamespace(
        id="r1",
        params_json={"symbol": symbol, "qty": 1, "tp_atr": 60},
        state_json={"live_real": live_real, "trend": "up", "position": 3},
    )


@pytest.mark.asyncio
async def test_roll_switches_symbol_and_resets_state(monkeypatch):
    async def fake_front(sym, today=None):
        return "RIU6"
    monkeypatch.setattr(cr, "front_contract", fake_front)
    sch = RobotScheduler(db_pool=None)
    r = _robot("RIM6")
    await sch._maybe_roll(r)
    assert r.params_json["symbol"] == "RIU6"          # rolled
    assert r.params_json["qty"] == 1                  # other params kept
    assert r.state_json == {"live_real": False}       # strategy state wiped
    assert sch._robot_states["r1"] == {"live_real": False}


@pytest.mark.asyncio
async def test_no_roll_when_already_front(monkeypatch):
    async def fake_front(sym, today=None):
        return "RIU6"
    monkeypatch.setattr(cr, "front_contract", fake_front)
    sch = RobotScheduler(db_pool=None)
    r = _robot("RIU6")
    await sch._maybe_roll(r)
    assert r.params_json["symbol"] == "RIU6"
    assert r.state_json["trend"] == "up"              # untouched


@pytest.mark.asyncio
async def test_never_roll_real_robot(monkeypatch):
    async def fake_front(sym, today=None):
        return "RIU6"
    monkeypatch.setattr(cr, "front_contract", fake_front)
    sch = RobotScheduler(db_pool=None)
    r = _robot("RIM6", live_real=True)
    await sch._maybe_roll(r)
    assert r.params_json["symbol"] == "RIM6"          # real robot NOT rolled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/lab/test_scheduler_roll.py -v`
Expected: FAIL with `AttributeError: 'RobotScheduler' object has no attribute '_maybe_roll'`.

- [ ] **Step 3: Write minimal implementation**

Add this method to `RobotScheduler` (in `trader/lab/scheduler.py`, e.g. right after `_run_robot_task`):

```python
    async def _maybe_roll(self, robot) -> None:
        """Roll a PAPER robot to the current front contract when its specific
        contract is no longer the front (e.g. after expiry). Flat + fresh state.
        Real robots are never auto-rolled (arming real money is human-initiated)."""
        from trader.lab.contract_roll import front_contract, base_of
        params = robot.params_json if isinstance(robot.params_json, dict) else {}
        symbol = params.get("symbol")
        if not symbol or base_of(symbol) is None:
            return  # base-code symbol (already rolls via continuous bars) or missing
        state = robot.state_json if isinstance(robot.state_json, dict) else {}
        if bool(state.get("live_real", False)):
            return  # never auto-roll a REAL robot
        front = await front_contract(symbol)
        if not front or front == symbol:
            return
        # ROLL: new symbol, flat, fresh strategy state (preserve only live_real).
        new_params = {**params, "symbol": front}
        new_state = {"live_real": bool(state.get("live_real", False))}
        robot.params_json = new_params
        robot.state_json = new_state
        self._robot_states[robot.id] = dict(new_state)
        if self._pool is not None:
            import json as _json
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE robots SET params_json=$1, state_json=$2 WHERE id=$3",
                    _json.dumps(new_params), _json.dumps(new_state), robot.id,
                )
        log.info("lab.roll", robot_id=robot.id, old=symbol, new=front)
```

Then call it at the very top of `_run_robot_task`, before the compile block:

```python
    async def _run_robot_task(self, robot) -> None:
        """Execute one robot tick (one bar)."""
        from trader.lab.runtime import LiveRuntime  # avoid import cycle
        from trader.lab.script_guard import validate_script
        # Follow the front contract across expiry so the robot never freezes on a
        # dead contract (paper robots only; real robots are rolled by a human).
        try:
            await self._maybe_roll(robot)
        except Exception as exc:
            log.warning("lab.roll_failed", robot_id=robot.id, error=str(exc))
        # Validate + compile once per script version, reuse the module across ticks.
        script_hash = hash(robot.script_code)
        ...
```

(Leave the rest of `_run_robot_task` unchanged — it already reads `robot.params_json` for `on_bar` and `self._robot_states` for `prev_state`, both now updated by the roll.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/lab/test_scheduler_roll.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add trader/lab/scheduler.py tests/lab/test_scheduler_roll.py
git commit -m "feat(lab): scheduler auto-rolls paper robots to the front contract"
```

---

### Task 3: Gap-simulation backfill script

**Files:**
- Create: `scripts/backfill_roll_sim.py` (thin CLI; the testable mapper `fills_to_rows` already lives in `trader/lab/contract_roll.py` from Task 1)

**Interfaces:**
- Consumes: `contract_roll.base_of`, `contract_roll.front_contract`, `contract_roll.fills_to_rows` (Task 1); `iss_loader.load_bars_iss`; `backtest.run_single_backtest`; `script_guard.validate_script`; `trader.db.init_pool/get_pool/close_pool`; `trader.config.settings.lab_db_url`
- Produces:
  - `async backfill_one(conn, robot: dict, *, dry_run: bool) -> tuple[str, int]`
  - `async main()` CLI entry

Note: no separate pytest file — the only non-trivial pure logic (`fills_to_rows`) is
tested in Task 1. The script is a thin DB/ISS orchestrator; it is verified end-to-end by
the `--dry-run` in Task 4 (scripts aren't a package here, so they run path-form
`poetry run python scripts/backfill_roll_sim.py`, matching the other scripts).

- [ ] **Step 1: Write the implementation**

```python
# scripts/backfill_roll_sim.py
"""One-shot: for each STALE paper Lab robot, simulate the strategy on its NEW front
contract over the gap since the old contract expired, and write the fills into
live_trades (status='paper', order_id='sim-...') so the showcase stats are
continuous. Also rolls the robot's params.symbol forward + resets state so the live
scheduler continues on the new contract.

Run on the hoster (heavy-compute isolation rule — NOT in the API process):
    poetry run python scripts/backfill_roll_sim.py            # apply
    poetry run python scripts/backfill_roll_sim.py --dry-run  # report only
    poetry run python scripts/backfill_roll_sim.py --robot <id>

Reversible:  DELETE FROM live_trades WHERE order_id LIKE 'sim-%';
"""
from __future__ import annotations

import argparse
import asyncio
import json
import types
from datetime import date, timedelta, timezone

from trader.config import settings
from trader.db import init_pool, get_pool, close_pool
from trader.lab.backtest import run_single_backtest
from trader.lab.contract_roll import base_of, front_contract, fills_to_rows
from trader.lab.iss_loader import load_bars_iss
from trader.lab.script_guard import validate_script

_MSK = timezone(timedelta(hours=3))


async def _last_trade_date(conn, robot_id: str, symbol: str):
    row = await conn.fetchrow(
        "SELECT max(timestamp) AS t FROM live_trades WHERE robot_id=$1 AND symbol=$2",
        robot_id, symbol)
    t = row["t"] if row else None
    return t.astimezone(_MSK).date() if t else None


async def backfill_one(conn, robot: dict, *, dry_run: bool) -> tuple[str, int]:
    params = robot["params_json"] if isinstance(robot["params_json"], dict) \
        else json.loads(robot["params_json"] or "{}")
    state = robot["state_json"] if isinstance(robot["state_json"], dict) \
        else json.loads(robot["state_json"] or "{}")
    if bool(state.get("live_real", False)):
        return ("skip-real", 0)
    old = params.get("symbol")
    if not old or base_of(old) is None:
        return ("skip-nonspecific", 0)
    new = await front_contract(old)
    if not new or new == old:
        return ("skip-current", 0)

    last_dt = await _last_trade_date(conn, robot["id"], old)
    date_from = (last_dt + timedelta(days=1)) if last_dt else (date.today() - timedelta(days=45))
    date_to = date.today()
    if date_from >= date_to:
        return ("skip-no-gap", 0)

    bars = await load_bars_iss(new, date_from, date_to, interval=1)
    if not bars:
        return (f"no-bars:{new}", 0)

    validate_script(robot["script_code"])
    mod = types.ModuleType("robot_script")
    exec(compile(robot["script_code"], f"<robot:{robot['id']}>", "exec"), mod.__dict__)
    sim_params = {**params, "symbol": new}
    res = await run_single_backtest(mod, bars, new, sim_params)
    fills = res.get("trades", [])
    if not fills:
        return (f"no-fills:{new}", 0)
    if dry_run:
        return (f"would {old}->{new} [{date_from}..{date_to}]", len(fills))

    rows = fills_to_rows(robot["id"], new, fills)
    async with conn.transaction():
        # idempotent: clear any prior sim rows for this robot on the new symbol
        await conn.execute(
            "DELETE FROM live_trades WHERE robot_id=$1 AND symbol=$2 AND order_id LIKE 'sim-%'",
            robot["id"], new)
        await conn.executemany(
            "INSERT INTO live_trades (id,robot_id,symbol,side,qty,price,order_id,status,timestamp)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", rows)
        # roll the robot forward so the live scheduler continues on `new`
        await conn.execute(
            "UPDATE robots SET params_json=$1, state_json=$2 WHERE id=$3",
            json.dumps(sim_params), json.dumps({"live_real": False}), robot["id"])
    return (f"sim {old}->{new} [{date_from}..{date_to}]", len(fills))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--robot", default=None, help="only this robot id")
    args = ap.parse_args()

    await init_pool(settings.lab_db_url)
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            if args.robot:
                rows = await conn.fetch("SELECT * FROM robots WHERE id=$1", args.robot)
            else:
                rows = await conn.fetch("SELECT * FROM robots")
            for r in rows:
                try:
                    status, n = await backfill_one(conn, dict(r), dry_run=args.dry_run)
                except Exception as exc:  # never let one robot abort the batch
                    status, n = (f"ERROR: {exc}", 0)
                print(f"{r['id']:<28} {r['name'][:32]:<34} {status}  fills={n}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify the script imports and shows help (no DB needed)**

Run: `poetry run python scripts/backfill_roll_sim.py --help`
Expected: argparse usage prints (imports resolve; `fills_to_rows` comes from `trader.lab.contract_roll`).

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_roll_sim.py
git commit -m "feat(lab): one-shot gap-simulation backfill for rolled paper robots"
```

---

### Task 4: Full-suite check + deploy

**Files:** none (verification + rollout)

- [ ] **Step 1: Run the lab test subset**

Run: `poetry run pytest tests/lab/ -q`
Expected: all pass (new roll/backfill tests + existing lab tests).

- [ ] **Step 2: Lint**

Run: `poetry run ruff check trader/lab/contract_roll.py trader/lab/scheduler.py scripts/backfill_roll_sim.py tests/lab/`
Expected: no errors.

- [ ] **Step 3: Deploy backend (auto-roll is code, needs the STL service)**

```bash
git push origin main
ssh hoster 'cd ~/apps/shectory-trader && git pull --ff-only'
# restart drops the agent gRPC link briefly — never while the operator live-tests trading
ssh hoster 'sudo systemctl restart shectory-trader'
```

- [ ] **Step 4: Dry-run the backfill on the hoster and eyeball it**

```bash
ssh hoster 'cd ~/apps/shectory-trader && /home/ubuntu/.local/bin/poetry run python -m scripts.backfill_roll_sim --dry-run'
```
Expected: a line per robot showing `would <old>-><new> [range]  fills=N` for stale ones, `skip-*` for current/real. Sanity-check a couple of old→new pairs (RIM6→RIU6, BRN6→ the current Brent front).

- [ ] **Step 5: Apply the backfill**

```bash
ssh hoster 'cd ~/apps/shectory-trader && /home/ubuntu/.local/bin/poetry run python -m scripts.backfill_roll_sim'
```
Expected: `sim <old>-><new>  fills=N` lines. The витрина statistics fill in on the next showcase refresh.

- [ ] **Step 6: Verify on the витрина**

Open `stl.shectory.ru/?lab=live` → Роботы. Previously-stale robots now show the new instrument tag, a non-empty P&L, and continued trades. Confirm no robot is still on an expired contract.

---

## Self-review notes

- Spec coverage: Unit 1 → Task 1; Unit 2 → Task 2; Unit 3 → Task 3; rollout → Task 4. All spec decisions (flat+reset, gap window from last-trade/expiry, `paper`+`sim-`, paper-only, ISS-failure guard, heavy-compute isolation, tz -3h) are encoded.
- Placeholder scan: none — every step has runnable code/commands.
- Type consistency: `base_of`/`front_contract` signatures identical across Tasks 1-3; `fills_to_rows` tuple order matches the INSERT column order in `backfill_one`.
