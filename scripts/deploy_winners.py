#!/usr/bin/env python3
"""
Deploy campaign WINNERS into paper trading.

After an optimization campaign fills `optimization_leaderboard`, this picks the
best parameter set per strategy (and optionally per symbol), creates/updates a
robot row with those params, and DEPLOYS it in PAPER mode (LiveRuntime defaults
to paper=True — no real orders, fills are recorded to live_trades as status='paper').

Winner = highest profit×recovery_factor among rows with net_profit > 0. Losing
strategies are skipped (nothing deployed).

Run ON THE VDS:
  cd ~/apps/shectory-trader
  poetry run python scripts/deploy_winners.py --strategies fvg,order_block,pivot_reversal
  # options: --campaign camp-YYYYMMDD-HHMM (default: latest), --per-symbol, --min-trades 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings  # noqa: E402
from trader.lab.strategies.library import REGISTRY  # noqa: E402


def _score(r) -> float:
    """profit×RF, mirroring the UI: losers (np<=0) rank below winners."""
    np = r["net_profit"] if r["net_profit"] is not None else (r["total_return"] or 0)
    if np <= 0:
        return np
    rf = r["recovery_factor"] or 0
    return np * max(rf, 0.01)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="",
                    help="comma list of strategy ids (default: all in REGISTRY)")
    ap.add_argument("--campaign", default="", help="campaign_run prefix (default: latest per strategy)")
    ap.add_argument("--per-symbol", action="store_true",
                    help="deploy the best per (strategy,symbol) instead of best per strategy")
    ap.add_argument("--top-n", type=int, default=0,
                    help="global top-N by profit×RF across all strategies (ignores --per-symbol)")
    ap.add_argument("--min-trades", type=int, default=30, help="ignore results with fewer trades")
    ap.add_argument("--schedule", default="09:00-23:55")
    args = ap.parse_args()
    want = [x.strip() for x in args.strategies.split(",") if x.strip()] if args.strategies else list(REGISTRY.keys())

    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url)
    async with pool.acquire() as c:
        await c.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

        link = await c.fetchrow("SELECT id, user_email FROM stl_links ORDER BY created_at LIMIT 1")
        if not link:
            print("no stl_links row — create a connector first"); return
        stl_link_id, user_email = link["id"], link["user_email"]

        # ── global top-N mode ────────────────────────────────────────────────
        if args.top_n:
            rows = await c.fetch(
                """SELECT DISTINCT ON (strategy, symbol)
                       strategy, symbol, params, net_profit, recovery_factor,
                       total_return, total_trades, sharpe, max_drawdown
                   FROM optimization_leaderboard
                   WHERE strategy = ANY($1::text[])
                     AND net_profit IS NOT NULL AND net_profit > 0
                     AND total_trades >= $2
                   ORDER BY strategy, symbol,
                            net_profit * GREATEST(recovery_factor, 0.01) DESC""",
                want, args.min_trades,
            )
            candidates = sorted(rows, key=_score, reverse=True)[: args.top_n]
            print(f"global top-{args.top_n}: {len(candidates)} candidates")

            deployed = 0
            for r in candidates:
                sid = r["strategy"]
                if sid not in REGISTRY:
                    print(f"  skip {sid}: not in REGISTRY"); continue
                params = dict(r["params"]) if isinstance(r["params"], dict) else json.loads(r["params"])
                params["symbol"] = r["symbol"]
                name = f"{REGISTRY[sid]['name']} (paper {r['symbol']})"
                script_code = f"from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('{sid}')"
                robot_id = f"paper-{sid}-{r['symbol']}"
                await c.execute(
                    """INSERT INTO robots
                         (id, user_email, stl_link_id, name, script_code, params_json,
                          state_json, schedule, deployed, deployed_at, version, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,'{}',$7,true, now(), 1, now())
                       ON CONFLICT (id) DO UPDATE SET
                         params_json=EXCLUDED.params_json, script_code=EXCLUDED.script_code,
                         name=EXCLUDED.name, deployed=true, deployed_at=now(),
                         state_json='{}', updated_at=now()""",
                    robot_id, user_email, stl_link_id, name, script_code, params, args.schedule,
                )
                deployed += 1
                print(f"  {robot_id}: net={r['net_profit']:.0f}₽ RF={r['recovery_factor'] or 0:.2f} "
                      f"trades={r['total_trades']} score={_score(r):.0f}")
            print(f"\nDEPLOYED {deployed} paper robot(s). Restart shectory-trader so the scheduler picks them up:")
            print("  sudo systemctl restart shectory-trader")
            await pool.close()
            return

        # ── per-strategy (original) mode ─────────────────────────────────────
        deployed = 0
        for sid in want:
            if sid not in REGISTRY:
                print(f"  skip {sid}: not in REGISTRY"); continue

            camp = args.campaign or await c.fetchval(
                "SELECT campaign_run FROM optimization_leaderboard "
                "WHERE strategy=$1 ORDER BY created_at DESC NULLS LAST LIMIT 1",
                sid,
            )
            if not camp:
                print(f"  {sid}: no campaign — skip"); continue

            rows = await c.fetch(
                """SELECT symbol, params, net_profit, recovery_factor, total_return,
                          total_trades, sharpe, max_drawdown
                   FROM optimization_leaderboard
                   WHERE campaign_run = $1 AND strategy = $2
                     AND net_profit IS NOT NULL AND net_profit > 0
                     AND total_trades >= $3""",
                camp, sid, args.min_trades,
            )
            if not rows:
                print(f"  {sid}: no profitable result (≥{args.min_trades} trades) — skip"); continue

            # best per symbol, or single best overall
            buckets: dict = {}
            for r in rows:
                key = r["symbol"] if args.per_symbol else "_best"
                if key not in buckets or _score(r) > _score(buckets[key]):
                    buckets[key] = r

            for key, r in buckets.items():
                params = dict(r["params"]) if isinstance(r["params"], dict) else json.loads(r["params"])
                params["symbol"] = r["symbol"]
                name = f"{REGISTRY[sid]['name']} (paper {r['symbol']})"
                script_code = f"from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('{sid}')"
                robot_id = f"paper-{sid}-{r['symbol']}"
                await c.execute(
                    """INSERT INTO robots
                         (id, user_email, stl_link_id, name, script_code, params_json,
                          state_json, schedule, deployed, deployed_at, version, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,'{}',$7,true, now(), 1, now())
                       ON CONFLICT (id) DO UPDATE SET
                         params_json=EXCLUDED.params_json, script_code=EXCLUDED.script_code,
                         name=EXCLUDED.name, deployed=true, deployed_at=now(),
                         state_json='{}', updated_at=now()""",
                    robot_id, user_email, stl_link_id, name, script_code, params, args.schedule,
                )
                deployed += 1
                print(f"  deployed {robot_id}: net={r['net_profit']:.0f}₽ RF={r['recovery_factor'] or 0:.2f} "
                      f"trades={r['total_trades']} params={ {k:v for k,v in params.items() if k!='symbol'} }")

        print(f"\nDEPLOYED {deployed} paper robot(s). Restart shectory-trader so the scheduler picks them up:")
        print("  sudo systemctl restart shectory-trader")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
