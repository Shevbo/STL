"""Queue the cooldown / superaverage R&D sweeps (2026-07-16).

For each REGISTRY strategy x instrument, take the top-3 configs (by Recovery
Factor among net_profit>0, total_trades>200) from the Apr-Jul 2026 leaderboard,
and queue two remote sweeps per base config:
  - cooldown:      base + cooldown_min in {0,10,20,...,300}   (cooldown_pct=1.0)
  - superaverage:  base + (super_y in 0..3) x (super_z in 0..5)

Standalone strategies (not in REGISTRY) and __inv variants are excluded — the
cooldown/super modifiers live in make_on_bar, which only the registry uses.

RUN ON THE HOSTER (has DB + API + auth secret):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PY=$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python
    $PY scripts/queue_cooldown_superavg.py --dry-run     # counts only
    $PY scripts/queue_cooldown_superavg.py --submit      # actually queue
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg
import httpx

from trader.auth.portal import make_session_token
from trader.lab.strategies.library import REGISTRY

SYMBOLS = {"RIU6", "BRQ6", "SiU6"}
DATE_FROM = "2026-03-31"
DATE_TO = "2026-07-10"
TOP_N = 3
API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

COOLDOWN_SETS = [{"cooldown_min": 0}] + [{"cooldown_min": n} for n in range(10, 301, 10)]
SUPER_SETS = [{"super_y": y, "super_z": z} for y in range(0, 4) for z in range(0, 6)]


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


async def top_configs(conn, min_trades: int = 200, max_trades: int = 0) -> list[dict]:
    """Top-N per strategy x symbol by RF among profitable, inside a trade-count band.
    max_trades=0 means no upper bound (the original >200 tier)."""
    rows = await conn.fetch(
        """SELECT strategy, symbol, params, net_profit, recovery_factor, total_trades
           FROM optimization_leaderboard
           WHERE total_trades > $1 AND ($2 = 0 OR total_trades <= $2) AND net_profit > 0
             AND recovery_factor IS NOT NULL
             AND date_from >= '2026-03-25' AND date_to <= '2026-07-31'
           ORDER BY strategy, symbol, recovery_factor DESC""", min_trades, max_trades)
    picked: dict[tuple[str, str], list[dict]] = {}
    seen: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        sid, sym = r["strategy"], r["symbol"]
        if sid not in REGISTRY or sym not in SYMBOLS:
            continue
        key = (sid, sym)
        raw = r["params"]
        pj = raw if isinstance(raw, str) else json.dumps(raw)
        if pj in seen.setdefault(key, set()):        # dedup identical param sets
            continue
        bucket = picked.setdefault(key, [])
        if len(bucket) >= TOP_N:
            continue
        seen[key].add(pj)
        bucket.append({"strategy": sid, "symbol": sym, "params": json.loads(pj),
                       "rf": float(r["recovery_factor"]), "net": float(r["net_profit"]),
                       "trades": int(r["total_trades"]), "rank": len(bucket) + 1})
    return [c for lst in picked.values() for c in lst]


def jobs_for(cfg: dict, tag: str = "") -> list[dict]:
    """tag distinguishes selection TIERS in the run_id (camp-<date>-<slug>-r..-strat-sym),
    so a second tier (e.g. the 1000-3000-trade band) never collides with, or is
    confused for, the first one in the leaderboard."""
    base = {**cfg["params"], "symbol": cfg["symbol"]}
    sc = script_code(cfg["strategy"])
    common = dict(scriptCode=sc, baseParams=base, symbol=cfg["symbol"],
                  dateFrom=DATE_FROM, dateTo=DATE_TO, engine="remote")
    return [
        {**common, "campaign": f"cooldown{tag}{cfg['rank']}", "paramSets": COOLDOWN_SETS},
        {**common, "campaign": f"superavg{tag}{cfg['rank']}", "paramSets": SUPER_SETS},
    ]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="queue only the first N base configs (canary)")
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument("--max-trades", type=int, default=0, help="0 = no upper bound")
    ap.add_argument("--tag", default="", help="campaign tier tag, e.g. 'mid' for the 1000-3000 band")
    args = ap.parse_args()

    conn = await asyncpg.connect(os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql"))
    cfgs = await top_configs(conn, args.min_trades, args.max_trades)
    await conn.close()

    cfgs.sort(key=lambda c: (-c["rf"], c["strategy"], c["symbol"]))   # canary picks strongest first
    if args.limit > 0:
        cfgs = cfgs[:args.limit]
    jobs = [j for c in cfgs for j in jobs_for(c, args.tag)]
    combos = sum(len(j["paramSets"]) for j in jobs)
    print(f"base configs: {len(cfgs)}  |  campaigns(jobs): {len(jobs)}  |  total backtests: {combos:,}")
    by_strat: dict[str, int] = {}
    for c in cfgs:
        by_strat[c["strategy"]] = by_strat.get(c["strategy"], 0) + 1
    for s in sorted(by_strat):
        print(f"  {s:<18} {by_strat[s]} configs")

    if args.dry_run or not args.submit:
        print("\nDry run — nothing submitted. Re-run with --submit to queue.")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=60) as cl:
        for i, job in enumerate(jobs, 1):
            try:
                r = cl.post("/api/v1/backtest/run", json=job)
                if r.status_code in (200, 201, 202):
                    ok += 1
                    if i % 20 == 0 or i == len(jobs):
                        print(f"  [{i}/{len(jobs)}] queued ({job['campaign']}) run={r.json().get('runId','?')}")
                else:
                    err += 1
                    print(f"  [{i}] HTTP {r.status_code}: {r.text[:120]}")
            except Exception as exc:  # noqa: BLE001
                err += 1
                print(f"  [{i}] error: {exc}")
    print(f"\nSubmitted {ok} campaigns, {err} errors, {combos:,} backtests queued.")


if __name__ == "__main__":
    asyncio.run(main())
