#!/usr/bin/env python3
"""
Enqueue a big optimization campaign as REMOTE jobs for the external agent (i9 host)
to chew through. Each (strategy × instrument) becomes one queued backtest_runs row
whose job_body carries the strategy's own script_code + base_params + a param grid,
so the agent runs it without needing a robots row per strategy.

Covers all 16 library strategies × the top FORTS instruments by turnover.
Grids are sized so the whole campaign is a few thousand combos — keeps each job
quick and the agent busy for a couple of hours.

Run ON THE VDS (has DB + ISS):
  cd ~/apps/shectory-trader
  poetry run python scripts/enqueue_campaign.py --instruments 8 --per-strategy 400
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import date, datetime, timezone
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings                              # noqa: E402
from trader.lab.strategies.library import REGISTRY, list_strategies  # noqa: E402

DATE_FROM = "2026-03-02T00:00:00Z"
DATE_TO = "2026-05-24T00:00:00Z"
# Any existing robot id — only needed to satisfy the robot_id FK; the agent uses
# script_code from job_body, not this robot.
FK_ROBOT_ID = "robot-supertrend-rts-01"


# Assets that MUST be in every campaign regardless of turnover ranking. RTS (the
# RI index future) is required in all backtests of all strategies.
ALWAYS_ASSETS = ["RTS"]


from trader.lab.iss_loader import top_instruments  # shared (was copy-pasted here)


def build_grid(strat: dict, cap: int) -> dict:
    """Coarse grid per strategy from its numeric params (min..max, ~4-5 steps each),
    random-sampled down to `cap` combos. Returns {param: [values...]} incl symbol."""
    nums = [p for p in strat["params_schema"] if p.get("type") == "number" and p["key"] != "qty"]
    axes = {}
    for p in nums:
        lo, hi = p.get("min", p["default"]), p.get("max", p["default"])
        if hi <= lo:
            axes[p["key"]] = [p["default"]]; continue
        step = max(1, round((hi - lo) / 4))
        axes[p["key"]] = list(range(int(lo), int(hi) + 1, step))[:5]
    keys = list(axes)
    combos = list(product(*[axes[k] for k in keys]))
    if len(combos) > cap:
        combos = random.sample(combos, cap)
    # transpose to {key: [values]} — but the run-task expands product again, so to
    # keep EXACTLY our sampled set we instead emit each param as its own axis only
    # when grid is full product; for sampled sets we pass them as explicit combos.
    return {"_keys": keys, "_combos": combos, "qty": 1}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", type=int, default=8)
    ap.add_argument("--per-strategy", type=int, default=400, help="combo cap per strategy×symbol")
    ap.add_argument("--campaign", default=datetime.now(timezone.utc).strftime("camp-%Y%m%d-%H%M"))
    args = ap.parse_args()

    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url)
    # JSON codec for JSONB columns
    async with pool.acquire() as conn:
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    syms = await top_instruments(args.instruments, always=ALWAYS_ASSETS)
    strategies = list_strategies()
    print(f"campaign {args.campaign}: {len(strategies)} strategies × {len(syms)} symbols")
    print("symbols:", ", ".join(syms))

    queued = 0
    async with pool.acquire() as conn:
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        for strat in strategies:
            g = build_grid(strat, args.per_strategy)
            # rebuild a per-param grid dict for the standard expansion path:
            # paramsGrid = {param: [values]} — full product. To honor the sampled
            # cap we instead enqueue ONE job per symbol carrying the explicit combos
            # via params_grid as lists per key (agent does product); cap keeps it small.
            keys, combos = g["_keys"], g["_combos"]
            # collapse sampled combos back to per-axis unique lists (product superset,
            # but bounded — each axis has ≤5 values so product ≤ a few hundred).
            grid = {k: sorted({c[i] for c in combos}) for i, k in enumerate(keys)}
            grid["qty"] = 1
            for sym in syms:
                run_id = f"{args.campaign}-{strat['id']}-{sym}".replace(" ", "")[:60]
                job_body = {
                    "engine": "remote", "symbol": sym,
                    "dateFrom": DATE_FROM, "dateTo": DATE_TO,
                    "paramsGrid": grid,
                    "script_code": strat["script_code"],
                    "base_params": {**strat["default_params"], "symbol": sym},
                }
                try:
                    await conn.execute(
                        """INSERT INTO backtest_runs
                             (id, robot_id, params_grid, date_from, date_to, status, engine, symbol, job_body)
                           VALUES ($1,$2,$3,$4,$5,'queued','remote',$6,$7)
                           ON CONFLICT (id) DO NOTHING""",
                        run_id, FK_ROBOT_ID, grid,
                        datetime.fromisoformat(DATE_FROM.replace("Z", "+00:00")),
                        datetime.fromisoformat(DATE_TO.replace("Z", "+00:00")),
                        sym, job_body,
                    )
                    queued += 1
                except Exception as exc:
                    print(f"  skip {run_id}: {exc}")
    print(f"queued {queued} jobs. Agent will process them. Watch optimization via backtest_runs/results.")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
