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
