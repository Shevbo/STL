"""Commission-aware parameter sweep for team-46, run on the i9.

Grid over the highest-leverage knobs (trigger selectivity + cooldown + agreement)
to find a config that overtrades less and survives FORTS fees. Each (combo, symbol)
is one backtest: proxy OFI, MAKER fees, 6 months. Score = summed NET across the
screening symbols. Baseline (current live defaults) is combo #0 for comparison.

    PYTHONPATH=. python scripts/run_ai46_sweep.py
"""
from __future__ import annotations

import itertools
import json
import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from trader.lab.ai46.backtest import Ai46Backtester
from trader.lab.ai46.params import BotParams
from trader.lab.runtime import Bar

DATA = os.path.join("data", "ai46_bt")
SYMBOLS = ["Si", "RI", "BR", "CC", "GD"]      # screening subset (fx/index/commodity mix)
STEP = 900
WINDOW = 2 * 86400
MODEL_WINDOW, MODEL_ITER, REFRESH = 240, 18, 7200.0

# Sweep grid (first value = current live default → combo #0 is the baseline).
GRID = {
    "ofi_thr":       [0.7, 0.85],
    "vol_thr":       [3.0, 5.0],
    "shock_z":       [2.0, 3.0],
    "min_agreement": [0.5, 0.75],
    "cooldown":      [300.0, 900.0],
}

_PV = json.load(open(os.path.join(DATA, "point_values.json"))) \
    if os.path.exists(os.path.join(DATA, "point_values.json")) else {}


def _load(key: str) -> list:
    with open(os.path.join(DATA, key + ".pkl"), "rb") as f:
        d = pickle.load(f)
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in d["rows"]]


def _combos() -> list:
    keys = list(GRID)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(GRID[k] for k in keys))]


def _worker(job):
    import asyncio
    cidx, fields, key = job
    bars = _load(key)
    params = BotParams(**fields)
    bt = Ai46Backtester(
        {key: bars}, step_secs=STEP, window_secs=WINDOW, ofi_mode="proxy",
        model_refresh_secs=REFRESH, model_window=MODEL_WINDOW, model_iter=MODEL_ITER,
        point_values={key: _PV.get(key, 1.0)}, taker=False, params=params,
    )
    m = asyncio.run(bt.run())
    return cidx, key, {"net": m["net_pnl"], "gross": m["gross_pnl"],
                       "fees": m["fees"], "trades": m["trades_closed"]}


def main() -> None:
    combos = _combos()
    jobs = [(i, c, s) for i, c in enumerate(combos) for s in SYMBOLS]
    workers = max(1, (os.cpu_count() or 4) - 2)
    print(f"combos={len(combos)} symbols={len(SYMBOLS)} jobs={len(jobs)} workers={workers}")
    print(f"grid={GRID}\nbaseline=combo#0 {combos[0]}")

    t0 = time.time()
    agg = {i: {"net": 0.0, "gross": 0.0, "fees": 0.0, "trades": 0, "n": 0}
           for i in range(len(combos))}
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_worker, j): j for j in jobs}
        for fut in as_completed(futs):
            cidx = futs[fut][0]
            try:
                _c, _k, r = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR combo#{cidx} {futs[fut][2]}: {type(exc).__name__}: {exc}")
                continue
            a = agg[cidx]
            a["net"] += r["net"]
            a["gross"] += r["gross"]
            a["fees"] += r["fees"]
            a["trades"] += r["trades"]
            a["n"] += 1
            done += 1
            if done % 20 == 0:
                print(f"  ...{done}/{len(jobs)}  ({time.time()-t0:.0f}s)")

    ranked = sorted(range(len(combos)), key=lambda i: agg[i]["net"], reverse=True)
    out = []
    for i in ranked:
        a = agg[i]
        out.append({"combo": combos[i], "net": round(a["net"], 5), "gross": round(a["gross"], 5),
                    "fees": round(a["fees"], 5), "trades": a["trades"], "symbols": a["n"]})
    path = os.path.join(DATA, "sweep_results.json")
    json.dump({"grid": GRID, "symbols": SYMBOLS, "ranked": out,
               "wall_secs": round(time.time()-t0, 1)}, open(path, "w"), ensure_ascii=False, indent=2)

    print(f"\n=== SWEEP LEADERBOARD by NET (maker, {len(SYMBOLS)} symbols, 6mo) ===")
    print(f"{'rank':>4} {'NET':>9} {'gross':>9} {'fees':>8} {'trades':>7}  params")
    for rank, i in enumerate(ranked):
        a = agg[i]
        base = " (BASELINE)" if i == 0 else ""
        print(f"{rank+1:>4} {a['net']:>+9.4f} {a['gross']:>+9.4f} {a['fees']:>8.4f} "
              f"{a['trades']:>7}  {combos[i]}{base}")
    print(f"\ntotal wall {time.time()-t0:.0f}s -> {path}")


if __name__ == "__main__":
    main()
