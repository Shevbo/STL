"""Enqueue the team-46 param sweep as a generic agent task (runs on the i9), then
poll for the result and print the NET leaderboard.

Auth: X-Agent-Token from env OPT_AGENT_TOKEN. API from env STL_API.
    OPT_AGENT_TOKEN=... PYTHONPATH=. python scripts/sweep_i9.py
"""
from __future__ import annotations

import itertools
import json
import os
import time
from datetime import date, timedelta

import httpx

API = os.environ.get("STL_API", "https://stl.shectory.ru").rstrip("/")
TOKEN = os.environ.get("OPT_AGENT_TOKEN", "")
H = {"X-Agent-Token": TOKEN, "Content-Type": "application/json"}

SYMBOLS = ["Si", "RI", "BR", "CC", "GD"]
GRID = {
    "ofi_thr":       [0.7, 0.85],
    "vol_thr":       [3.0, 5.0],
    "shock_z":       [2.0, 3.0],
    "min_agreement": [0.5, 0.75],
    "cooldown":      [300.0, 900.0],
}
CFG = {"step": 900, "window_days": 2, "model_window": 240, "model_iter": 18,
       "refresh": 7200.0, "ofi_mode": "proxy", "taker": False}
DAYS = 190


def main() -> None:
    if not TOKEN:
        raise SystemExit("set OPT_AGENT_TOKEN")
    pvs = json.load(open(os.path.join("data", "ai46_bt", "point_values.json")))
    today = date.today()
    d_from, d_to = str(today - timedelta(days=DAYS)), str(today)
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    args = [{"key": s, "fields": c, "date_from": d_from, "date_to": d_to,
             "point_value": pvs.get(s, 1.0), "cfg": CFG}
            for c in combos for s in SYMBOLS]
    tid = f"sweep-{d_to.replace('-', '')}"
    print(f"enqueue {tid}: {len(combos)} combos × {len(SYMBOLS)} symbols = {len(args)} units")

    r = httpx.post(f"{API}/api/v1/agent/task/enqueue", headers=H, timeout=60,
                   json={"id": tid, "module": "trader.lab.ai46.sweep_task",
                         "func": "run_combo", "args": args})
    print("enqueue ->", r.status_code, r.text[:200])
    r.raise_for_status()

    last = None
    for _ in range(480):                  # up to ~2h (poll 15s)
        time.sleep(15)
        g = httpx.get(f"{API}/api/v1/agent/task/{tid}", headers=H, timeout=30).json()
        if g["status"] != last:
            print(f"  status={g['status']} agent={g.get('agent_id')} claimed={g.get('claimed_at')}")
            last = g["status"]
        if g["status"] in ("done", "failed"):
            break
    if g["status"] != "done":
        print("NOT DONE:", g.get("error") or g["status"])
        return

    results = g.get("result") or []
    combos_list = combos
    agg = {i: {"net": 0.0, "gross": 0.0, "fees": 0.0, "trades": 0, "n": 0, "err": 0}
           for i in range(len(combos_list))}

    def _combo_idx(fields):
        for i, c in enumerate(combos_list):
            if c == fields:
                return i
        return None

    for row in results:
        i = _combo_idx(row.get("combo", {}))
        if i is None:
            continue
        a = agg[i]
        if row.get("error"):
            a["err"] += 1
            continue
        a["net"] += row.get("net", 0.0)
        a["gross"] += row.get("gross", 0.0)
        a["fees"] += row.get("fees", 0.0)
        a["trades"] += row.get("trades", 0)
        a["n"] += 1

    ranked = sorted(range(len(combos_list)), key=lambda i: agg[i]["net"], reverse=True)
    out = [{"combo": combos_list[i], **{k: round(agg[i][k], 5) if isinstance(agg[i][k], float)
            else agg[i][k] for k in agg[i]}} for i in ranked]
    json.dump({"grid": GRID, "symbols": SYMBOLS, "cfg": CFG, "ranked": out},
              open(os.path.join("data", "ai46_bt", "sweep_i9_results.json"), "w"),
              ensure_ascii=False, indent=2)

    print(f"\n=== i9 SWEEP LEADERBOARD by NET (maker, {len(SYMBOLS)} symbols, 6mo) ===")
    print(f"{'rank':>4} {'NET':>9} {'gross':>9} {'fees':>8} {'trades':>7}  params")
    for rank, i in enumerate(ranked):
        a = agg[i]
        base = " (BASELINE)" if combos_list[i] == {k: GRID[k][0] for k in GRID} else ""
        print(f"{rank+1:>4} {a['net']:>+9.4f} {a['gross']:>+9.4f} {a['fees']:>8.4f} "
              f"{a['trades']:>7}  {combos_list[i]}{base}")


if __name__ == "__main__":
    main()
