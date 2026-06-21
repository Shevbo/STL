"""Parallel team-46 container backtest: one process per (symbol, ofi_mode).

Each job runs a SINGLE-symbol Ai46Backtester (its own runner/exec/risk). Note:
the cross-symbol portfolio cap (max 5 positions / 30% exposure) is therefore NOT
applied — every symbol is evaluated standalone. This gives clean per-symbol
attribution and near-linear speedup (HMM is pure-CPU). A portfolio run with
shared risk is a separate, sequential follow-up.

    PYTHONPATH=. python scripts/run_ai46_bt_par.py --mode both
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from trader.lab.ai46.backtest import Ai46Backtester
from trader.lab.runtime import Bar

DATA = os.path.join("data", "ai46_bt")


def _load(key: str) -> list:
    with open(os.path.join(DATA, key + ".pkl"), "rb") as f:
        d = pickle.load(f)
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in d["rows"]]


def _all_keys() -> list:
    return sorted(f[:-4] for f in os.listdir(DATA)
                  if f.endswith(".pkl") and not f.startswith("ticks_"))


def _worker(job):
    key, mode, cfg = job
    bars = _load(key)
    if cfg["days"] > 0 and bars:
        cutoff = bars[-1].time - cfg["days"] * 86400
        bars = [b for b in bars if b.time >= cutoff]
    pv = cfg.get("point_values", {}).get(key, 1.0)
    bt = Ai46Backtester(
        {key: bars}, step_secs=cfg["step"], window_secs=cfg["window_days"] * 86400,
        ofi_mode=mode, model_refresh_secs=cfg["refresh"], model_window=cfg["model_window"],
        model_iter=cfg["model_iter"], llm_enabled=False, blend_tick=cfg["blend"],
        point_values={key: pv}, taker=cfg.get("taker", True),
    )
    t0 = time.time()
    m = asyncio.run(bt.run())
    m["wall_secs"] = round(time.time() - t0, 1)
    return key, mode, m


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="all")
    p.add_argument("--mode", default="both", choices=["zero", "proxy", "both"])
    p.add_argument("--step", type=int, default=600)
    p.add_argument("--window-days", type=int, default=2, dest="window_days")
    p.add_argument("--model-window", type=int, default=240, dest="model_window")
    p.add_argument("--model-iter", type=int, default=18, dest="model_iter")
    p.add_argument("--refresh", type=float, default=7200.0)
    p.add_argument("--days", type=int, default=0)
    p.add_argument("--blend", action="store_true")
    p.add_argument("--maker", action="store_true", help="maker fees (broker only) instead of taker")
    p.add_argument("--workers", type=int, default=0)
    args = p.parse_args()

    keys = _all_keys() if args.symbols == "all" else [s.strip() for s in args.symbols.split(",")]
    modes = ["zero", "proxy"] if args.mode == "both" else [args.mode]
    pv_path = os.path.join(DATA, "point_values.json")
    point_values = json.load(open(pv_path)) if os.path.exists(pv_path) else {}
    cfg = {"step": args.step, "window_days": args.window_days, "model_window": args.model_window,
           "model_iter": args.model_iter, "refresh": args.refresh, "days": args.days,
           "blend": args.blend, "point_values": point_values, "taker": not args.maker}
    jobs = [(k, m, cfg) for m in modes for k in keys]
    workers = args.workers or max(1, (os.cpu_count() or 4) - 2)
    print(f"jobs={len(jobs)} ({len(keys)} symbols x {len(modes)} modes)  workers={workers}")
    print(f"cfg step={args.step}s window={args.window_days}d model_window={args.model_window} "
          f"iter={args.model_iter} refresh={args.refresh}s days={args.days or 'full'}")

    t0 = time.time()
    results = {m: {} for m in modes}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_worker, j): (j[0], j[1]) for j in jobs}
        done = 0
        for fut in as_completed(futs):
            key, mode = futs[fut]
            try:
                _k, _m, metrics = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR {key}/{mode}: {type(exc).__name__}: {exc}")
                continue
            results[mode][key] = metrics
            done += 1
            d = metrics["per_symbol"].get(key, {})
            print(f"[{done}/{len(jobs)}] {mode:5} {key:9} "
                  f"tr={metrics['trades_closed']:>4} win={metrics['win_rate']:.2f} "
                  f"gross={metrics['gross_pnl']:+.4f} fees={metrics['fees']:.4f} "
                  f"NET={metrics['net_pnl']:+.4f} L{d.get('longs',0)}/S{d.get('shorts',0)} "
                  f"{metrics['wall_secs']}s")

    # portfolio rollup per mode (sum of standalone per-symbol results)
    summary = {}
    for mode in modes:
        rs = list(results[mode].values())
        tot_gross = sum(r["gross_pnl"] for r in rs)
        tot_fees = sum(r["fees"] for r in rs)
        tot_net = sum(r["net_pnl"] for r in rs)
        tot_tr = sum(r["trades_closed"] for r in rs)
        tot_win = sum(r["per_symbol"].get(k, {}).get("wins", 0) for k, r in results[mode].items())
        longs = sum(d.get("longs", 0) for r in rs for d in r["per_symbol"].values())
        shorts = sum(d.get("shorts", 0) for r in rs for d in r["per_symbol"].values())
        summary[mode] = {"gross": round(tot_gross, 5), "fees": round(tot_fees, 5),
                         "net": round(tot_net, 5), "trades": tot_tr,
                         "win_rate": round(tot_win / tot_tr, 4) if tot_tr else 0.0,
                         "longs": longs, "shorts": shorts}

    out = {"cfg": cfg, "summary": summary, "results": results,
           "wall_secs": round(time.time() - t0, 1)}
    path = os.path.join(DATA, f"bt_results_{'maker' if args.maker else 'taker'}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n=== PORTFOLIO SUMMARY (standalone sum, no cross-symbol cap, FORTS taker fees) ===")
    for mode, s in summary.items():
        print(f"  {mode:5} NET={s['net']:+.4f} (gross={s['gross']:+.4f} fees={s['fees']:.4f}) "
              f"trades={s['trades']} win={s['win_rate']:.2f} L{s['longs']}/S{s['shorts']}")
    print(f"\ntotal wall {out['wall_secs']}s -> {path}")


if __name__ == "__main__":
    main()
