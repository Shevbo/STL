"""Run the team-46 container backtest over cached 1m bars.

    PYTHONPATH=. python scripts/run_ai46_bt.py --symbols Si,GD --mode both \
        --step 300 --window-days 7 --model-window 600 --refresh 1800 --days 0

--symbols  comma list of cache keys, or 'all'
--mode     zero | proxy | both
--days     keep only the last N days of bars per symbol (0 = full ~6 months)
Outputs per-run timing + metrics (per symbol and portfolio).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
import time

from trader.lab.ai46.backtest import Ai46Backtester
from trader.lab.runtime import Bar

DATA = os.path.join("data", "ai46_bt")


def _load(key: str) -> list[Bar]:
    with open(os.path.join(DATA, key + ".pkl"), "rb") as f:
        d = pickle.load(f)
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in d["rows"]]


def _all_keys() -> list[str]:
    return sorted(f[:-4] for f in os.listdir(DATA) if f.endswith(".pkl"))


async def _run_one(bars_by, mode, args) -> dict:
    bt = Ai46Backtester(
        bars_by, step_secs=args.step, window_secs=args.window_days * 86400,
        ofi_mode=mode, model_refresh_secs=args.refresh, model_window=args.model_window,
        llm_enabled=args.llm, blend_tick=args.blend,
    )
    t0 = time.time()
    m = await bt.run()
    m["wall_secs"] = round(time.time() - t0, 1)
    return m


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="Si,GD")
    p.add_argument("--mode", default="both", choices=["zero", "proxy", "both"])
    p.add_argument("--step", type=int, default=300)
    p.add_argument("--window-days", type=int, default=7, dest="window_days")
    p.add_argument("--model-window", type=int, default=600, dest="model_window")
    p.add_argument("--refresh", type=float, default=1800.0)
    p.add_argument("--days", type=int, default=0, help="keep last N days only (0=full)")
    p.add_argument("--llm", action="store_true")
    p.add_argument("--blend", action="store_true")
    args = p.parse_args()

    keys = _all_keys() if args.symbols == "all" else [s.strip() for s in args.symbols.split(",")]
    bars_by = {}
    for k in keys:
        b = _load(k)
        if args.days > 0 and b:
            cutoff = b[-1].time - args.days * 86400
            b = [x for x in b if x.time >= cutoff]
        bars_by[k] = b
    tot = sum(len(v) for v in bars_by.values())
    print(f"loaded {len(bars_by)} symbols, {tot} bars; "
          f"step={args.step}s window={args.window_days}d model_window={args.model_window} "
          f"refresh={args.refresh}s days={args.days or 'full'}")

    modes = ["zero", "proxy"] if args.mode == "both" else [args.mode]
    out = {}
    for mode in modes:
        m = asyncio.run(_run_one({k: list(v) for k, v in bars_by.items()}, mode, args))
        out[mode] = m
        per = m.pop("per_symbol", {})
        print(f"\n=== mode={mode}  wall={m['wall_secs']}s  ticks={m['ticks']} ===")
        print(json.dumps(m, ensure_ascii=False))
        for sym, d in per.items():
            print(f"  {sym:10} open={d['opens']:>3} (L{d['longs']}/S{d['shorts']}) "
                  f"close={d['closes']:>3} win={d['wins']:>3} pnl={d['pnl']:+.4f}")


if __name__ == "__main__":
    main()
