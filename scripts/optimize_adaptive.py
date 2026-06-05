#!/usr/bin/env python3
"""
Adaptive optimizer campaign — coarse-to-fine parameter search (REMOTE/agent flow).

Tactic the user asked for: cast a WIDE random net over the full parameter space to
catch "golden" zones fast, then ZOOM IN — refine a fine local grid around the best
(recovery-factor x return) results, repeated for a couple of rounds. Wide+big-step
first, narrow+small-step after.

Stages per (strategy x instrument):
  r0  EXPLORE : --explore random combos across each numeric param's FULL schema range
  r1..rD REFINE: take --top winners by score = return x recovery_factor (filters:
                 return>0, RF >= --rf-min, drawdown <= --dd-max, trades in range),
                 build a fine local grid (shrinking window + finer step) around each,
                 union + cap, run. Each round zooms tighter.

Jobs are enqueued as REMOTE (opt-...) for the i9 agent to compute (or the throttled
VDS fallback if the agent is down). Results are mirrored metrics-only into
optimization_leaderboard, so Botstore shows them like any campaign.

This SUPERSEDES the old in-process optimize_campaign.py (which ran backtests on the
VDS itself and overloaded it). Run ON THE VDS (has DB + ISS):
  cd ~/apps/shectory-trader
  poetry run python scripts/optimize_adaptive.py --instruments 15 --explore 300 --rounds 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings                              # noqa: E402
from trader.lab.strategies.library import list_strategies      # noqa: E402

DATE_FROM = "2026-03-02T00:00:00Z"
DATE_TO = "2026-05-24T00:00:00Z"
FK_ROBOT_ID = "robot-supertrend-rts-01"   # FK filler; the agent uses job_body.script_code
ALWAYS_ASSETS = ["RTS"]                    # RTS/RI forced into every campaign


async def top_instruments(n: int, always: list[str] | None = None) -> list[str]:
    """Top-n FORTS futures by today's turnover (front contract per asset), via ISS.
    Front contracts of `always` assets (e.g. RTS/RI) are force-included."""
    import httpx
    url = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
           "?iss.meta=off&iss.only=securities,marketdata"
           "&securities.columns=SECID,ASSETCODE,LASTTRADEDATE"
           "&marketdata.columns=SECID,VALTODAY")
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "STL/1.0"}) as c:
        j = (await c.get(url)).json()
    sec, md = j.get("securities", {}), j.get("marketdata", {})
    turn = {dict(zip(md["columns"], r)).get("SECID"): (dict(zip(md["columns"], r)).get("VALTODAY") or 0)
            for r in md.get("data", [])}
    by_asset: dict = {}
    for row in sec.get("data", []):
        d = dict(zip(sec["columns"], row))
        sid, asset, ltd = d.get("SECID"), d.get("ASSETCODE"), d.get("LASTTRADEDATE")
        if not sid or not asset:
            continue
        vt = turn.get(sid, 0) or 0
        cur = by_asset.get(asset)
        if cur is None:
            by_asset[asset] = {"front": sid, "ltd": ltd, "turn": vt}
        else:
            cur["turn"] += vt
            if ltd and (cur["ltd"] is None or ltd < cur["ltd"]):
                cur["front"], cur["ltd"] = sid, ltd
    ranked = sorted(by_asset.values(), key=lambda x: x["turn"], reverse=True)
    syms = [a["front"] for a in ranked[:n] if a["turn"] > 0]
    for asset in (always or []):
        a = by_asset.get(asset)
        if a and a["front"] not in syms:
            syms.append(a["front"])
    return syms


def numeric_params(strat: dict) -> list[dict]:
    """Tunable numeric params (excludes qty/symbol). Each: key, lo, hi."""
    out = []
    for p in strat["params_schema"]:
        if p.get("type") != "number" or p["key"] == "qty":
            continue
        lo = int(p.get("min", p["default"]))
        hi = int(p.get("max", p["default"]))
        if hi < lo:
            lo, hi = hi, lo
        out.append({"key": p["key"], "lo": lo, "hi": hi})
    return out


def random_combos(nums: list[dict], n: int, rng: random.Random) -> list[dict]:
    """n DISTINCT random points over the full ranges (the wide net)."""
    if not nums:
        return [{}]
    seen, out = set(), []
    attempts, max_attempts = 0, n * 20    # cap so a tiny integer space can't loop forever
    while len(out) < n and attempts < max_attempts:
        attempts += 1
        combo = {p["key"]: rng.randint(p["lo"], p["hi"]) for p in nums}
        key = tuple(sorted(combo.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(combo)
    return out


def refine_grid(center: dict, nums: list[dict], rnd: int, steps: int) -> list[dict]:
    """Fine local grid around `center`. Window halves each round (r1: span/4 each side,
    r2: span/8, ...); step = window/(steps-1). Always includes the center value."""
    axes = {}
    for p in nums:
        k, lo, hi = p["key"], p["lo"], p["hi"]
        c = int(center.get(k, (lo + hi) // 2))
        span = hi - lo
        half = max(1, round(span / (2 * (2 ** rnd))))   # r1 -> span/4 each side
        a = max(lo, c - half)
        b = min(hi, c + half)
        step = max(1, round((b - a) / max(1, steps - 1)))
        vals = sorted(set(list(range(a, b + 1, step)) + [c]))
        axes[k] = vals
    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in product(*[axes[k] for k in keys])]


def passes(row: dict, args) -> bool:
    ret = row.get("total_return") or 0
    rf = row.get("recovery_factor")
    dd = row.get("max_drawdown")
    tr = row.get("total_trades") or 0
    return (ret > args.ret_min and rf is not None and rf >= args.rf_min
            and (dd is None or dd <= args.dd_max)
            and args.trades_min <= tr <= args.trades_max)


def winner_score(row: dict) -> float:
    """Golden = high return AND high recovery factor. Both positive past the filters."""
    return (row.get("total_return") or 0) * max(row.get("recovery_factor") or 0, 0)


async def _enqueue(conn, run_id, symbol, strat, param_sets):
    job_body = {
        "engine": "remote", "symbol": symbol,
        "dateFrom": DATE_FROM, "dateTo": DATE_TO,
        "script_code": strat["script_code"],
        "base_params": {**strat["default_params"], "symbol": symbol},
        "param_sets": param_sets,            # explicit combos (random / unioned grids)
    }
    await conn.execute(
        """INSERT INTO backtest_runs
             (id, robot_id, params_grid, date_from, date_to, status, engine, symbol, job_body)
           VALUES ($1,$2,$3,$4,$5,'queued','remote',$6,$7)
           ON CONFLICT (id) DO NOTHING""",
        run_id, FK_ROBOT_ID, {},
        datetime.fromisoformat(DATE_FROM.replace("Z", "+00:00")),
        datetime.fromisoformat(DATE_TO.replace("Z", "+00:00")),
        symbol, job_body,
    )


async def _wait_phase(pool, campaign: str, rnd: int, poll: float):
    """Block until every job of round `rnd` is done/failed (queued+running == 0)."""
    prefix = f"{campaign}-r{rnd}-"
    while True:
        rows = await pool.fetch(
            "SELECT status, count(*) AS n FROM backtest_runs WHERE id LIKE $1 GROUP BY status",
            prefix + "%",
        )
        if not rows:
            return
        by = {r["status"]: r["n"] for r in rows}
        pending = by.get("queued", 0) + by.get("running", 0)
        print(f"  r{rnd}: done={by.get('done', 0)} failed={by.get('failed', 0)} pending={pending}",
              flush=True)
        if pending == 0:
            return
        await asyncio.sleep(poll)


async def _winners(pool, campaign, strat_id, symbol, args) -> list[dict]:
    rows = await pool.fetch(
        """SELECT params, total_return, recovery_factor, max_drawdown, total_trades
           FROM optimization_leaderboard
           WHERE campaign_run=$1 AND strategy=$2 AND symbol=$3""",
        campaign, strat_id, symbol,
    )
    cand = [dict(r) for r in rows if passes(dict(r), args)]
    cand.sort(key=winner_score, reverse=True)
    cand = cand[: args.top]
    for c in cand:                       # defensive: params must be a dict for refine_grid
        if isinstance(c.get("params"), str):
            c["params"] = json.loads(c["params"])
    return cand


async def main():
    ap = argparse.ArgumentParser(description="Adaptive coarse-to-fine optimizer campaign")
    ap.add_argument("--instruments", type=int, default=15)
    ap.add_argument("--explore", type=int, default=300, help="random combos per strat x symbol (r0)")
    ap.add_argument("--rounds", type=int, default=2, help="refine rounds (r1..rD)")
    ap.add_argument("--top", type=int, default=5, help="winners to refine around, per strat x symbol")
    ap.add_argument("--refine-steps", type=int, default=5, help="grid values per param per round")
    ap.add_argument("--max-combos", type=int, default=400, help="cap combos per refine job")
    ap.add_argument("--rf-min", type=float, default=1.5)
    ap.add_argument("--dd-max", type=float, default=0.15)
    ap.add_argument("--ret-min", type=float, default=0.0)
    ap.add_argument("--trades-min", type=int, default=30)
    ap.add_argument("--trades-max", type=int, default=5000)
    ap.add_argument("--poll", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--campaign", default=datetime.now(timezone.utc).strftime("opt-%Y%m%d-%H%M"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    s = Settings()

    # JSON codec on EVERY pooled connection (init=), so JSONB params/job_body decode to
    # dicts no matter which connection serves the query. (Setting it on one acquired
    # connection left pool.fetch() returning params as raw strings → refine crashed
    # with "'str' object has no attribute 'get'".)
    async def _init_codec(conn):
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    pool = await asyncpg.create_pool(s.lab_db_url, init=_init_codec)

    syms = await top_instruments(args.instruments, always=ALWAYS_ASSETS)
    strategies = list_strategies()
    print(f"campaign {args.campaign}: {len(strategies)} strategies x {len(syms)} symbols")
    print("symbols:", ", ".join(syms), flush=True)

    # ── r0: EXPLORE (random wide net) ────────────────────────────────────────
    print(f"[r0 EXPLORE] {args.explore} random combos / strat x symbol", flush=True)
    queued = 0
    async with pool.acquire() as conn:
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        for strat in strategies:
            nums = numeric_params(strat)
            for sym in syms:
                combos = random_combos(nums, args.explore, rng)
                run_id = f"{args.campaign}-r0-{strat['id']}-{sym}".replace(" ", "")[:60]
                await _enqueue(conn, run_id, sym, strat, combos)
                queued += 1
    print(f"  queued {queued} explore jobs", flush=True)
    await _wait_phase(pool, args.campaign, 0, args.poll)

    # ── r1..rD: REFINE (zoom around winners) ─────────────────────────────────
    for rnd in range(1, args.rounds + 1):
        print(f"[r{rnd} REFINE] top-{args.top} by return x RF, "
              f"window span/{2 * (2 ** rnd)} each side", flush=True)
        queued = 0
        async with pool.acquire() as conn:
            await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
            for strat in strategies:
                nums = numeric_params(strat)
                for sym in syms:
                    winners = await _winners(pool, args.campaign, strat["id"], sym, args)
                    if not winners:
                        continue
                    union: dict[tuple, dict] = {}
                    for w in winners:
                        for combo in refine_grid(w["params"], nums, rnd, args.refine_steps):
                            union[tuple(sorted(combo.items()))] = combo
                    combos = list(union.values())
                    if len(combos) > args.max_combos:
                        combos = rng.sample(combos, args.max_combos)
                    run_id = f"{args.campaign}-r{rnd}-{strat['id']}-{sym}".replace(" ", "")[:60]
                    await _enqueue(conn, run_id, sym, strat, combos)
                    queued += 1
        print(f"  queued {queued} refine jobs", flush=True)
        if queued == 0:
            print("  no winners passed the filters — stopping refine.", flush=True)
            break
        await _wait_phase(pool, args.campaign, rnd, args.poll)

    # ── summary: best per strategy ───────────────────────────────────────────
    print("\n=== BEST PER STRATEGY (this campaign) ===", flush=True)
    rows = await pool.fetch(
        """SELECT DISTINCT ON (strategy) strategy, symbol, params, total_return,
                  recovery_factor, max_drawdown, total_trades
           FROM optimization_leaderboard
           WHERE campaign_run=$1
           ORDER BY strategy, (total_return * GREATEST(coalesce(recovery_factor,0),0)) DESC""",
        args.campaign,
    )
    for r in rows:
        print(f"  {r['strategy']:<16} {r['symbol']:<7} ret={float(r['total_return'] or 0)*100:6.2f}% "
              f"RF={r['recovery_factor']} dd={float(r['max_drawdown'] or 0)*100:5.2f}% "
              f"trades={r['total_trades']} {json.dumps(r['params'])}", flush=True)
    print(f"\ncampaign {args.campaign} done.", flush=True)
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
