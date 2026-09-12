"""Профиль одного прогона rich_fool: куда уходит время на i9.

Повод: волна rf7 идёт 112 прогонов/мин, 500k это ~74 часа. Подозрение — _step_sizes
(подбор множителя делением отрезка, 80 итераций) пересчитывается на КАЖДОМ баре
внутри окна, хотя за день распределение объёма не меняется.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_profile.py [SECID] [дней]
"""
from __future__ import annotations

import asyncio
import cProfile
import datetime
import importlib
import io
import json
import pstats
import sys
import time

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

PARAMS = dict(f_shift=70, n_days=10, hold_min=120, sl_beyond_pts=50, tp_arm_pts=400,
              tp_back_pts=150, qty_first=1, max_contracts=40, d_coef=175,
              step_count=20, place_lead_min=10, slip_guard_pts=50, slip_pct=0,
              ema_fast=9, ema_slow=21, exit_lead_min=120, invert=0,
              allow_long=1, allow_short=1, bar_offset_min=0)


def load(secid: str, days: int) -> list[Bar]:
    rows = json.load(open(f"agent_bars/{secid}.json"))["rows"]
    hi = int(datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp())
    lo = hi - days * 86400
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows if lo <= r[0] <= hi]


async def one(mod, bars, secid):
    return await run_single_backtest(mod, bars, secid, {**PARAMS, "symbol": secid},
                                     point_value=1.0)


def main() -> None:
    secid = sys.argv[1] if len(sys.argv) > 1 else "RIU6"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    bars = load(secid, days)
    t0 = time.perf_counter()
    asyncio.run(one(mod, bars, secid))
    wall = time.perf_counter() - t0
    print(f"{secid}: {len(bars)} баров, {days} дней, один прогон {wall:.2f} с")

    pr = cProfile.Profile()
    pr.enable()
    asyncio.run(one(mod, bars, secid))
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(14)
    print(s.getvalue())

    # доля _step_sizes отдельно
    t0 = time.perf_counter()
    for _ in range(2000):
        mod._step_sizes(40, 20, 1)
    per = (time.perf_counter() - t0) / 2000
    print(f"_step_sizes: {per * 1e6:.1f} мкс за вызов")


if __name__ == "__main__":
    main()
