"""One-day team-46 backtest with FORTS commission, comparing OFI modes:
  zero  (OFI=0, baseline)  vs  proxy (CLV from bars)  vs  real (actual ticks).

Real ticks exist only for the latest ISS session, so the clock is bounded to that
day; bars carry ~10 days of history for feature lookback. Reports gross / fees /
net per symbol and portfolio for each mode.

    PYTHONPATH=. python scripts/run_ai46_bt_day.py --symbols Si,BR,RI
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pickle

from trader.lab.ai46.backtest import Ai46Backtester
from trader.lab.runtime import Bar

DATA = os.path.join("data", "ai46_bt")
_LOOKBACK_DAYS = 10


def _load_bars(key: str) -> list:
    with open(os.path.join(DATA, key + ".pkl"), "rb") as f:
        d = pickle.load(f)
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in d["rows"]]


def _load_ticks(key: str):
    with open(os.path.join(DATA, f"ticks_{key}.pkl"), "rb") as f:
        d = pickle.load(f)
    return d["rows"], d["point_value"], d["date"]


async def _run(mode, bars_by, ticks_by, pvs, t0, t1, args) -> dict:
    return await Ai46Backtester(
        {k: list(v) for k, v in bars_by.items()},
        step_secs=args.step, window_secs=7 * 86400, ofi_mode=mode,
        model_refresh_secs=args.refresh, model_window=args.model_window,
        model_iter=args.model_iter, llm_enabled=False,
        ticks_by_symbol=(ticks_by if mode == "real" else None),
        point_values=pvs, taker=True, clock_from=t0, clock_to=t1,
    ).run()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="Si,BR,RI")
    p.add_argument("--step", type=int, default=60)
    p.add_argument("--model-window", type=int, default=300, dest="model_window")
    p.add_argument("--model-iter", type=int, default=30, dest="model_iter")
    p.add_argument("--refresh", type=float, default=600.0)
    args = p.parse_args()

    keys = [s.strip() for s in args.symbols.split(",")]
    bars_by, ticks_by, pvs = {}, {}, {}
    t0 = t1 = None
    for k in keys:
        bars = _load_bars(k)
        ticks, pv, date = _load_ticks(k)
        cutoff = bars[-1].time - _LOOKBACK_DAYS * 86400
        bars_by[k] = [b for b in bars if b.time >= cutoff]
        ticks_by[k] = ticks
        pvs[k] = pv
        tk0, tk1 = ticks[0][0], ticks[-1][0]
        t0 = tk0 if t0 is None else min(t0, tk0)
        t1 = tk1 if t1 is None else max(t1, tk1)
        print(f"{k}: bars(lookback)={len(bars_by[k])} ticks={len(ticks)} pv={pv} date={date} "
              f"last_bar_close={bars[-1].close} last_tick_px={ticks[-1][1]}")

    print(f"clock {t0}..{t1}  step={args.step}s  model(iter={args.model_iter},win={args.model_window})\n")
    rows = {}
    for mode in ("zero", "proxy", "real"):
        m = asyncio.run(_run(mode, bars_by, ticks_by, pvs, t0, t1, args))
        rows[mode] = m
        print(f"=== {mode:5} ticks={m['ticks']} trades={m['trades_closed']} win={m['win_rate']:.2f} "
              f"L{sum(d['longs'] for d in m['per_symbol'].values())}/"
              f"S{sum(d['shorts'] for d in m['per_symbol'].values())} ===")
        print(f"      gross={m['gross_pnl']:+.5f}  fees={m['fees']:.5f}  NET={m['net_pnl']:+.5f}  "
              f"maxDD(net)={m['max_drawdown']:.5f}")
        for sym, d in m["per_symbol"].items():
            print(f"        {sym:4} tr={d['closes']:>3} gross={d['pnl']:+.5f} "
                  f"fees={d['fees']:.5f} net={d['net']:+.5f} L{d['longs']}/S{d['shorts']}")
        print()

    print("=== NET PnL by mode (day, after FORTS taker commission) ===")
    for mode in ("zero", "proxy", "real"):
        m = rows[mode]
        print(f"  {mode:5} net={m['net_pnl']:+.5f}  gross={m['gross_pnl']:+.5f}  "
              f"fees={m['fees']:.5f}  trades={m['trades_closed']}")


if __name__ == "__main__":
    main()
