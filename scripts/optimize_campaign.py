#!/usr/bin/env python3
"""
LAB optimization campaign — 5h unattended parameter sweep across all strategies
and cached symbols, building a leaderboard of candidates for real (live) launch.

Strategy:
  - For each (strategy, symbol): enumerate the VALID parameter grid.
  - "Every 3rd variant by random selection" → randomly keep ~1/3 of the grid.
  - "Large grids → random search" → if the kept set exceeds MAX_PER_TASK, sample
    that many uniformly at random.
  - Round-robin across tasks so every strategy/symbol gets coverage even if the
    5h budget runs out before any single task finishes.
  - Backtests run IN-PROCESS (no subprocess) — bars stay in memory, fast.
  - Every result is stored in optimization_leaderboard with a transparent score
    and a `candidate` flag for combos good enough to consider live.

Run:  python scripts/optimize_campaign.py [hours]   (default 5)
"""

from __future__ import annotations

import asyncio
import importlib
import os
import random
import sys
import time
from datetime import date, datetime, timezone
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.config import Settings                       # noqa: E402
from trader.lab.backtest import run_single_backtest      # noqa: E402
from trader.lab.market_store import get_bars, upsert_bars  # noqa: E402
from trader.lab.iss_loader import load_bars_iss          # noqa: E402

import asyncpg  # noqa: E402

# ── campaign config ──────────────────────────────────────────────────────────
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
TIME_LIMIT = HOURS * 3600
KEEP_FRACTION = 1 / 3        # take every ~3rd variant (random)
MAX_PER_TASK = 4000          # cap random-search budget per (strategy, symbol)
BATCH = 30                   # combos per round-robin slice (for time checks)
SYMBOLS = ["RIM6", "SiM6", "GZM6"]
DATE_FROM = date(2026, 3, 1)
DATE_TO = date(2026, 5, 25)
INITIAL_EQUITY = 100_000.0

# candidate (real-launch) quality bar
def is_candidate(m: dict) -> bool:
    return (
        (m.get("total_return") or 0) > 0
        and m.get("sharpe") is not None and m["sharpe"] >= 0.5
        and m.get("max_drawdown") is not None and m["max_drawdown"] <= 0.15
        and 30 <= (m.get("total_trades") or 0) <= 3000
    )

def score_of(m: dict) -> float:
    return (m.get("sharpe") or 0) + 3 * (m.get("total_return") or 0) - 2 * (m.get("max_drawdown") or 0)

# ── strategy parameter spaces ────────────────────────────────────────────────
SPECS = {
    "ema_crossover": {
        "module": "trader.lab.strategies.ema_crossover",
        "space": {"fast_period": range(3, 41), "slow_period": range(8, 151)},
        "valid": lambda p: p["fast_period"] < p["slow_period"],
        "base": {"qty": 1},
    },
    "rsi_mean_reversion": {
        "module": "trader.lab.strategies.rsi_mean_reversion",
        "space": {"period": range(5, 41), "oversold": range(10, 41, 2), "overbought": range(60, 91, 2)},
        "valid": lambda p: p["oversold"] < p["overbought"],
        "base": {},
    },
    "donchian_breakout": {
        "module": "trader.lab.strategies.donchian_breakout",
        "space": {"entry_period": range(5, 151), "exit_period": range(2, 81)},
        "valid": lambda p: p["exit_period"] < p["entry_period"],
        "base": {"qty": 1},
    },
    "supertrend": {
        "module": "trader.lab.strategies.supertrend",
        "space": {"atr_period": range(5, 61), "multiplier": range(8, 91)},
        "valid": lambda p: True,
        "base": {"qty": 1},
    },
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def build_combos(spec: dict, symbol: str) -> list[dict]:
    keys = list(spec["space"].keys())
    vals = [list(spec["space"][k]) for k in keys]
    combos = []
    for tup in product(*vals):
        p = dict(zip(keys, tup))
        if spec["valid"](p):
            p.update(spec["base"])
            p["symbol"] = symbol
            combos.append(p)
    random.shuffle(combos)
    # take every ~3rd (random subset), then cap to budget
    keep = max(1, int(len(combos) * KEEP_FRACTION))
    combos = combos[:keep]
    if len(combos) > MAX_PER_TASK:
        combos = random.sample(combos, MAX_PER_TASK)
    return combos


async def ensure_table(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS optimization_leaderboard (
                id            BIGSERIAL PRIMARY KEY,
                campaign_run  TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                symbol        TEXT NOT NULL,
                params        JSONB NOT NULL,
                total_return  DOUBLE PRECISION,
                sharpe        DOUBLE PRECISION,
                max_drawdown  DOUBLE PRECISION,
                win_rate      DOUBLE PRECISION,
                total_trades  INTEGER,
                score         DOUBLE PRECISION,
                candidate     BOOLEAN DEFAULT false,
                created_at    TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_lb_score ON optimization_leaderboard(score DESC);
            CREATE INDEX IF NOT EXISTS idx_lb_candidate ON optimization_leaderboard(candidate, score DESC);
        """)


async def ensure_bars(pool, symbol: str) -> list:
    bars = await get_bars(pool, symbol, DATE_FROM, DATE_TO)
    if bars:
        log(f"{symbol}: {len(bars)} bars in cache")
        return bars
    log(f"{symbol}: not cached, fetching from ISS…")
    try:
        bars = await load_bars_iss(symbol, DATE_FROM, DATE_TO, interval=1)
        if bars:
            await upsert_bars(pool, symbol, bars)
            log(f"{symbol}: fetched + cached {len(bars)} bars")
    except Exception as exc:
        log(f"{symbol}: ISS fetch failed: {exc}")
        bars = []
    return bars


async def main() -> None:
    import json
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log(f"=== campaign {stamp} | budget {HOURS}h ===")

    dsn = Settings().lab_db_url
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=6)
    await ensure_table(pool)

    # load data + strategy modules
    bars_by_symbol: dict[str, list] = {}
    for sym in SYMBOLS:
        b = await ensure_bars(pool, sym)
        if b:
            bars_by_symbol[sym] = b

    modules = {sid: importlib.import_module(spec["module"]) for sid, spec in SPECS.items()}

    # build tasks
    tasks = []
    for sid, spec in SPECS.items():
        for sym in bars_by_symbol:
            combos = build_combos(spec, sym)
            tasks.append({"sid": sid, "sym": sym, "combos": combos, "i": 0})
            log(f"task {sid}/{sym}: {len(combos)} combos queued")

    t0 = time.monotonic()
    done = 0
    cand = 0
    rows_buf = []

    async def flush():
        if not rows_buf:
            return
        async with pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO optimization_leaderboard
                   (campaign_run, strategy, symbol, params, total_return, sharpe,
                    max_drawdown, win_rate, total_trades, score, candidate)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                rows_buf,
            )
        rows_buf.clear()

    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= TIME_LIMIT:
            log(f"time budget reached ({elapsed:.0f}s)")
            break
        any_work = False
        for task in tasks:
            if task["i"] >= len(task["combos"]):
                continue
            if time.monotonic() - t0 >= TIME_LIMIT:
                break
            any_work = True
            sid, sym = task["sid"], task["sym"]
            mod = modules[sid]
            bars = bars_by_symbol[sym]
            batch = task["combos"][task["i"]: task["i"] + BATCH]
            task["i"] += len(batch)
            for params in batch:
                try:
                    r = await run_single_backtest(mod, bars, sym, params, INITIAL_EQUITY)
                except Exception:
                    continue
                m = {
                    "total_return": r.get("total_return"),
                    "sharpe": r.get("sharpe"),
                    "max_drawdown": r.get("max_drawdown"),
                    "win_rate": r.get("win_rate"),
                    "total_trades": r.get("total_trades"),
                }
                c = is_candidate(m)
                if c:
                    cand += 1
                rows_buf.append((
                    stamp, sid, sym, json.dumps(params),
                    m["total_return"], m["sharpe"], m["max_drawdown"],
                    m["win_rate"], m["total_trades"], score_of(m), c,
                ))
                done += 1
            if len(rows_buf) >= 100:
                await flush()
            if done % 200 == 0 and done:
                log(f"done={done} candidates={cand} elapsed={int(time.monotonic()-t0)}s "
                    f"({task['sid']}/{task['sym']} {task['i']}/{len(task['combos'])})")
        if not any_work:
            log("all tasks exhausted")
            break

    await flush()
    # final summary: top candidates
    async with pool.acquire() as conn:
        top = await conn.fetch(
            """SELECT strategy, symbol, params, total_return, sharpe, max_drawdown,
                      win_rate, total_trades, score
               FROM optimization_leaderboard
               WHERE campaign_run=$1 AND candidate=true
               ORDER BY score DESC LIMIT 20""", stamp)
    log(f"=== DONE: evaluated={done} candidates={cand} ===")
    log("TOP CANDIDATES (score desc):")
    for r in top:
        log(f"  {r['strategy']:<20} {r['symbol']} {r['params']} "
            f"ret={(r['total_return'] or 0)*100:.2f}% sh={r['sharpe']:.2f} "
            f"dd={(r['max_drawdown'] or 0)*100:.1f}% win={(r['win_rate'] or 0)*100:.0f}% "
            f"n={r['total_trades']} score={r['score']:.3f}")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
