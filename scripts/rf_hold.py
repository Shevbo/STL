"""rich_fool_hold против rich_fool на шести кварталах RI: итог, пик контрактов, риск.

HOLD держит каждую книгу до тейка или стопа, ночь не закрывает. Отчёт на квартал:
итог закрытых сделок (₽, с комиссией), открытое на конце окна (₽ по последнему
закрытию), пик суммы контрактов по книгам и пик ЧИСТОЙ позиции, пик числа книг,
худшая открытая просадка (MTM) и сколько книг закрылось тейком и стопом.

ЗАПУСК: PYTHONPATH=.:scripts $PY scripts/rf_hold.py [--invert 1]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib

import queue_rich_fool as Q
import rf_day_walk as W

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import BacktestRuntime

QUARTERS = ["RIM5", "RIU5", "RIZ5", "RIH6", "RIM6", "RIU6"]
PV = 1.42
# Ближайший к кандидату вектор rf10 (13.09): бюджет 40, тейк 700/150.
VEC = dict(W.LEADER, f_shift=90, n_days=4, hold_min=45, sl_beyond_pts=150, time_exit_min=120,
           tp_arm_pts=700, tp_back_pts=150, qty_first=2, max_contracts=40)


async def hold_stats(mod, bars, sec, p):
    """Прогон HOLD вручную: пики из состояния, MTM по каждому бару, судьба книг."""
    rt = BacktestRuntime(bars=bars, symbol=sec, initial_equity=1_000_000.0, point_value=PV)
    peak_eq, max_dd, n_books_prev, closes = rt._equity, 0.0, 0, 0
    while True:
        bar = bars[rt._cursor]
        await mod.on_bar(rt, p)
        pos = rt._positions.get(sec, {"side": "flat", "qty": 0, "avg": 0.0})
        signed = pos["qty"] if pos["side"] == "long" else -pos["qty"] if pos["side"] == "short" else 0
        mtm = rt._equity + signed * (bar.close - pos["avg"]) * PV
        peak_eq = max(peak_eq, mtm)
        max_dd = max(max_dd, peak_eq - mtm)
        nb = len(rt._state.get("books") or [])
        closes += max(0, n_books_prev - nb)
        n_books_prev = nb
        if not rt.advance():
            break
    books = rt._state.get("books") or []
    last = bars[rt._cursor].close
    open_rub = sum((last - b["cost"] / b["q"]) * b["dir"] * b["q"] for b in books) * PV
    return rt, max_dd, open_rub, books


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invert", type=int, default=0)
    a = ap.parse_args()
    base = importlib.import_module("trader.lab.strategies.rich_fool")
    hold = importlib.import_module("trader.lab.strategies.rich_fool_hold")
    print(f"вектор: F={VEC['f_shift'] / 10} n={VEC['n_days']} окно={VEC['hold_min']} "
          f"тейк={VEC['tp_arm_pts']}/{VEC['tp_back_pts']} стоп за лестницей {VEC['sl_beyond_pts']} "
          f"q1={VEC['qty_first']} бюджет/книга={VEC['max_contracts']} invert={a.invert}")
    print(f"\n{'квартал':<8}{'ИСХОДНАЯ ₽':>12}{'HOLD закр ₽':>13}{'HOLD откр ₽':>13}{'HOLD итог':>11}"
          f"{'пик сумм':>9}{'пик нетто':>10}{'книг max':>9}{'MTM прос':>10}{'откр книг':>10}")
    tot_b = tot_h = 0.0
    for sec in QUARTERS:
        d_from, d_to = W.WINDOWS[sec]
        bars = W.load(sec)
        d = Q._d_coef(Q._day_stats(sec, d_from, d_to), VEC["n_days"], Q.STEP_COUNT, VEC["f_shift"] / 10)
        p = dict(VEC, d_coef=d, invert=a.invert, symbol=sec)
        r0 = await run_single_backtest(base, bars, sec, p, point_value=PV)
        rt, dd, open_rub, books = await hold_stats(hold, bars, sec, p)
        closed = rt._equity - 1_000_000.0
        tot_b += r0["net_profit"]
        tot_h += closed + open_rub
        st = rt._state
        print(f"{sec:<8}{r0['net_profit']:>12,.0f}{closed:>13,.0f}{open_rub:>13,.0f}{closed + open_rub:>11,.0f}"
              f"{st.get('max_gross', 0):>9}{st.get('max_net', 0):>10}{st.get('max_books', 0):>9}"
              f"{dd:>10,.0f}{len(books):>10}")
    print(f"\nсумма шести: исходная {tot_b:,.0f} ₽, HOLD {tot_h:,.0f} ₽ (открытое по последней цене окна)")


if __name__ == "__main__":
    asyncio.run(main())
