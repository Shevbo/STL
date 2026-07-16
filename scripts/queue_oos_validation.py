"""Stage 4: OUT-OF-SAMPLE validation of the cooldown/superaverage winners.

Everything so far was chosen IN-SAMPLE on 2026-03-30..07-10 — the same window the
base configs were already optimised on. Picking the best cooldown_min out of 30 and
the best (y,z) out of 24 on that same data is selection bias, so the in-sample gains
are part real effect, part curve fit. This re-runs each winner on a window that took
NO part in the selection: 2026-01-05..2026-03-25.

The Sep-expiry contracts (RIU6/SiU6/BRQ6) were a thin far month back then, so the
OOS uses the CONTINUOUS front-month splice of the same underlying:
    RIU6 -> RI,  SiU6 -> Si.
BRQ6 is SKIPPED: continuous BR has no data in that window (53 bars) — the BRQ6
winners (which happen to be the biggest in-sample gains) cannot be validated here.

Per config it queues 4 param sets on the OOS window:
    baseline (no modifiers) | best cooldown | best superaverage | best hybrid
so the OOS comparison is apples-to-apples against the config's own baseline.

RUN ON THE HOSTER:
    PYTHONPATH=~/apps/shectory-trader $PY scripts/queue_oos_validation.py --dry-run
    PYTHONPATH=~/apps/shectory-trader $PY scripts/queue_oos_validation.py --submit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg
import httpx

from trader.auth.portal import make_session_token

OOS_FROM = "2026-01-05"
OOS_TO = "2026-03-25"
CONT = {"RIU6": "RI", "SiU6": "Si"}      # BRQ6 intentionally absent: no OOS data
API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
MOD_KEYS = ("cooldown_min", "cooldown_pct", "super_y", "super_z")


def P(x):
    return json.loads(x) if isinstance(x, str) else x


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


async def best_of(c, kind: str, keyf, offf) -> dict:
    """{(strat,sym,rank): {"base":params,"base_rf":rf,"best_k":k,"best_rf":rf}}"""
    out: dict = {}
    for r in await c.fetch(
            "select id from backtest_runs where id like $1 and status='done'",
            f"camp-20260716-{kind}%"):
        parts = r["id"].split("-")
        strat, sym, rank = parts[-2], parts[-1], parts[2][-1]
        base_rf = base_p = None
        best_rf = best_k = None
        for x in await c.fetch(
                "select params,net_profit,recovery_factor from backtest_results where run_id=$1",
                r["id"]):
            p = P(x["params"])
            rf, net = float(x["recovery_factor"] or 0), float(x["net_profit"] or 0)
            k = keyf(p)
            if offf(k):
                if base_rf is None or rf > base_rf:
                    base_rf = rf
                    base_p = {kk: vv for kk, vv in p.items() if kk not in MOD_KEYS}
            elif net > 0 and (best_rf is None or rf > best_rf):
                best_rf, best_k = rf, k
        if base_p and best_k is not None:
            out[(strat, sym, rank)] = {"base": base_p, "base_rf": base_rf,
                                       "best_k": best_k, "best_rf": best_rf}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    c = await asyncpg.connect(os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql"))
    cd = await best_of(c, "cooldown", lambda p: p.get("cooldown_min"), lambda k: k == 0)
    sa = await best_of(c, "superavg", lambda p: (p.get("super_y"), p.get("super_z")),
                       lambda k: (not k[0] or not k[1]))
    hy = await best_of(c, "hybrid", lambda p: (p.get("cooldown_min"), p.get("super_y"), p.get("super_z")),
                       lambda k: k == (0, 0, 0))
    await c.close()

    jobs, skipped = [], 0
    for key in sorted(set(cd) | set(sa)):
        strat, sym, rank = key
        if sym not in CONT:
            skipped += 1
            continue
        cd_win = key in cd and cd[key]["best_rf"] > cd[key]["base_rf"] + 0.01
        sa_win = key in sa and sa[key]["best_rf"] > sa[key]["base_rf"] + 0.01
        if not (cd_win or sa_win):
            continue
        base = dict((cd.get(key) or sa[key])["base"])
        oos_sym = CONT[sym]
        base["symbol"] = oos_sym
        sets = [{"cooldown_min": 0, "super_y": 0, "super_z": 0}]
        if cd_win:
            sets.append({"cooldown_min": cd[key]["best_k"], "super_y": 0, "super_z": 0})
        if sa_win:
            y, z = sa[key]["best_k"]
            sets.append({"cooldown_min": 0, "super_y": y, "super_z": z})
        if key in hy and hy[key]["best_rf"] > hy[key]["base_rf"] + 0.01:
            n, y, z = hy[key]["best_k"]
            sets.append({"cooldown_min": n, "super_y": y, "super_z": z})
        jobs.append({"campaign": f"oos{rank}", "scriptCode": script_code(strat),
                     "baseParams": base, "paramSets": sets, "symbol": oos_sym,
                     "dateFrom": OOS_FROM, "dateTo": OOS_TO, "engine": "remote"})

    combos = sum(len(j["paramSets"]) for j in jobs)
    print(f"OOS window {OOS_FROM}..{OOS_TO} | campaigns: {len(jobs)}  backtests: {combos}")
    print(f"skipped (no OOS data for symbol, e.g. BRQ6): {skipped}")
    if args.dry_run or not args.submit:
        print("Dry run — nothing submitted.")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=60) as cl:
        for i, job in enumerate(jobs, 1):
            r = cl.post("/api/v1/backtest/run", json=job)
            ok += 1 if r.status_code in (200, 201, 202) else 0
            err += 0 if r.status_code in (200, 201, 202) else 1
            if i % 15 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}] {r.status_code}")
    print(f"submitted {ok}, errors {err}, {combos} OOS backtests queued")


if __name__ == "__main__":
    asyncio.run(main())
