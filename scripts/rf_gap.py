"""Фильтр гэпа rich_fool: порог подбирается ТОЛЬКО на RIM6/RIU6, проверяется на остальных.

gap_skip_steps=k: если цена открытия дня уже за k-й ступенью, лестница в этот день не
ставится (0 = фильтр выключен). Порог выбирается по худшему из RIM6/RIU6 для фейда;
RIM5/RIU5/RIZ5/RIH6 в выбор не входят и показывают, переносится ли он.

ЗАПУСК: PYTHONPATH=.:scripts $PY scripts/rf_gap.py
"""
from __future__ import annotations

import asyncio
import importlib
from concurrent.futures import ProcessPoolExecutor

import queue_rich_fool as Q
import rf_day_walk as W

from trader.lab.backtest import run_single_backtest

KS = [0, 1, 2, 3, 4, 6, 8]
IN = ["RIM6", "RIU6"]
OUT = ["RIM5", "RIU5", "RIZ5", "RIH6"]
# Ближайший к кандидату вектор rf10 (13.09): бюджет 40, тейк 700/150.
VEC = dict(W.LEADER, f_shift=90, n_days=4, hold_min=45, sl_beyond_pts=150, time_exit_min=120,
           tp_arm_pts=700, tp_back_pts=150, qty_first=2, max_contracts=40)


def one(job):
    sec, k, inv = job
    d_from, d_to = W.WINDOWS[sec]
    bars = W.load(sec)
    d = Q._d_coef(Q._day_stats(sec, d_from, d_to), VEC["n_days"], Q.STEP_COUNT, VEC["f_shift"] / 10)
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    p = dict(VEC, d_coef=d, invert=inv, gap_skip_steps=k, symbol=sec)
    r = asyncio.run(run_single_backtest(mod, bars, sec, p, point_value=1.42))
    return job, r["net_profit"], r["total_trades"]


def main() -> None:
    jobs = [(s, k, i) for s in IN + OUT for k in KS for i in (0, 1)]
    with ProcessPoolExecutor(max_workers=6) as ex:
        res = {j: (n, t) for j, n, t in ex.map(one, jobs)}

    def row(k, inv):
        return [res[(s, k, inv)] for s in IN + OUT]

    print("фейд, ₽ с комиссией (сделок)")
    print(f"{'k':>3} " + "".join(f"{s:>15}" for s in IN + OUT))
    for k in KS:
        print(f"{k:>3} " + "".join(f"{n:>9,.0f} ({t:>2})" for n, t in row(k, 0)))
    print("\nзеркало, ₽")
    for k in KS:
        print(f"{k:>3} " + "".join(f"{n:>9,.0f} ({t:>2})" for n, t in row(k, 1)))

    best = max(KS, key=lambda k: min(res[(s, k, 0)][0] for s in IN))
    print(f"\nВЫБОР ПО RIM6/RIU6 (худший из двух): k={best}")
    for k in sorted({0, best}):
        f = [res[(s, k, 0)][0] for s in OUT]
        m = [res[(s, k, 1)][0] for s in OUT]
        ok = sum(1 for a, b in zip(f, m) if a > 0 and a > b)
        print(f"  k={k}: вне выборки фейд " + ", ".join(f"{s} {x:,.0f}" for s, x in zip(OUT, f))
              + f" | сумма {sum(f):,.0f} | кварталов фейд>0 и >зеркала: {ok} из 4")


if __name__ == "__main__":
    main()
