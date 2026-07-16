"""Stage 3: queue the cooldown+superaverage HYBRID sweep on the WINNERS.

A winner = a base config whose cooldown OR superaverage sweep beat its own
baseline on Recovery Factor. For each winner we cross its TOP-3 cooldown_min
values with its TOP-3 (super_y, super_z) pairs (9 combos + a no-modifier
control) instead of the full 30x24=720 grid — the focused scope the operator
chose.

Also re-queues runs that died on the VDS fallback ("Empty": it could not fetch
bars; the i9 has them).

RUN ON THE HOSTER:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PY=$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python
    PYTHONPATH=~/apps/shectory-trader $PY scripts/queue_hybrid_cooldown_superavg.py --dry-run
    PYTHONPATH=~/apps/shectory-trader $PY scripts/queue_hybrid_cooldown_superavg.py --submit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg
import httpx

from trader.auth.portal import make_session_token

DATE_FROM = "2026-03-31"
DATE_TO = "2026-07-10"
API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
TOP_K = 3          # top-3 N x top-3 (y,z) -> 9 hybrid combos per winner
MOD_KEYS = ("cooldown_min", "cooldown_pct", "super_y", "super_z")


def P(x):
    return json.loads(x) if isinstance(x, str) else x


def strip_mods(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in MOD_KEYS}


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


async def requeue_failed(c) -> int:
    res = await c.execute(
        """UPDATE backtest_runs SET status='queued', agent_id=NULL, claimed_at=NULL, error_msg=NULL
           WHERE (id LIKE 'camp-20260716-cooldown%' OR id LIKE 'camp-20260716-superavg%')
             AND status='failed'""")
    return int(res.split()[-1]) if res else 0


async def collect(c, kind: str) -> dict:
    """{(strategy,symbol,rank): {"base": params, "tops": [...], "base_rf": float}}"""
    like = f"camp-20260716-{kind}%"
    out: dict = {}
    for r in await c.fetch("select id from backtest_runs where id like $1 and status='done'", like):
        parts = r["id"].split("-")
        strat, sym = parts[-2], parts[-1]
        rank = parts[2][-1]
        res = await c.fetch(
            "select params,net_profit,recovery_factor from backtest_results where run_id=$1", r["id"])
        base_rf, base_params, cands = None, None, []
        for x in res:
            p = P(x["params"])
            rf = float(x["recovery_factor"] or 0)
            net = float(x["net_profit"] or 0)
            if kind == "cooldown":
                k = p.get("cooldown_min")
                off = (k == 0)
            else:
                k = (p.get("super_y"), p.get("super_z"))
                off = (not k[0] or not k[1])
            if off:
                if base_rf is None or rf > base_rf:
                    base_rf, base_params = rf, strip_mods(p)
            elif net > 0:
                cands.append((rf, k))
        if base_params is None or not cands:
            continue
        cands.sort(reverse=True)
        out[(strat, sym, rank)] = {"base": base_params, "base_rf": base_rf or 0.0,
                                   "tops": [k for _, k in cands[:TOP_K]],
                                   "best_rf": cands[0][0]}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql"))
    n_req = await requeue_failed(c)
    print(f"re-queued failed runs: {n_req}")

    cd = await collect(c, "cooldown")
    sa = await collect(c, "superavg")
    await c.close()

    # Winner = improved on RF under EITHER modifier; hybrid needs both grids present.
    winners = []
    for key in set(cd) & set(sa):
        cd_win = cd[key]["best_rf"] > cd[key]["base_rf"] + 0.01
        sa_win = sa[key]["best_rf"] > sa[key]["base_rf"] + 0.01
        if cd_win or sa_win:
            winners.append((key, cd[key], sa[key]))
    print(f"winners (cooldown OR superavg improved): {len(winners)}")

    jobs = []
    for (strat, sym, rank), cdi, sai in winners:
        sets = [{"cooldown_min": 0, "super_y": 0, "super_z": 0}]      # control
        for n in cdi["tops"]:
            for (y, z) in sai["tops"]:
                sets.append({"cooldown_min": n, "super_y": y, "super_z": z})
        jobs.append({"campaign": f"hybrid{rank}", "scriptCode": script_code(strat),
                     "baseParams": {**cdi["base"], "symbol": sym}, "paramSets": sets,
                     "symbol": sym, "dateFrom": DATE_FROM, "dateTo": DATE_TO, "engine": "remote"})
    combos = sum(len(j["paramSets"]) for j in jobs)
    print(f"hybrid campaigns: {len(jobs)}  backtests: {combos:,}")

    if args.dry_run or not args.submit:
        print("Dry run — nothing submitted.")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=60) as cl:
        for i, job in enumerate(jobs, 1):
            try:
                r = cl.post("/api/v1/backtest/run", json=job)
                ok += 1 if r.status_code in (200, 201, 202) else 0
                err += 0 if r.status_code in (200, 201, 202) else 1
                if i % 20 == 0 or i == len(jobs):
                    print(f"  [{i}/{len(jobs)}] {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                err += 1
                print(f"  [{i}] {exc}")
    print(f"submitted {ok}, errors {err}, {combos:,} backtests queued")


if __name__ == "__main__":
    asyncio.run(main())
