"""Диагностика rich_fool: почему при 21-26% дней с достижением 1-й ступени
в лидерборде всего 2-5 сделок за полгода. Гипотеза: позиция висит овернайт
неделями (day_done=1 + выход только по TP/SL), и лестница простаивает.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_diag_holdtime.py
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import json

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

D_FROM, D_TO = "2026-03-09", "2026-09-09"


def load(symbol: str) -> list[Bar]:
    rows = json.load(open(f"agent_bars/{symbol}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(D_FROM).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(D_TO).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows if lo <= r[0] <= hi]


def ts(t: int) -> str:
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


async def main() -> None:
    bars = load("RI")
    print(f"баров: {len(bars)}  {ts(bars[0].time)} .. {ts(bars[-1].time)}")
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    p = dict(qty=1, symbol="RI", open_hour=10, open_min=0, place_lead_min=10,
             hold_min=30, n_days=5, step_count=5, sl_pct=30, rr_x10=20,
             vol_mult=15, max_contracts=120, invert=0, allow_long=1,
             allow_short=1, bar_offset_min=0)
    res = await run_single_backtest(mod, bars, "RI", p, point_value=1.0)
    print(f"net={res['net_profit']:,.0f} trades={res['total_trades']} fills={len(res['trades'])}")

    tr = sorted(res["trades"], key=lambda t: t["time"])
    print("\n--- все филлы ---")
    for t in tr:
        print(f"  {ts(t['time'])}  {t['side']:<4} qty={t['qty']:<3} px={t['price']:,.0f}")

    # сколько дней позиция была ненулевой: вход -> выход
    pos, entry = 0, None
    print("\n--- время удержания ---")
    for t in tr:
        sign = 1 if t["side"] == "buy" else -1
        was = pos
        pos += sign * t["qty"]
        if was == 0 and pos != 0:
            entry = t["time"]
        elif was != 0 and pos == 0 and entry is not None:
            days = (t["time"] - entry) / 86400
            print(f"  {ts(entry)} -> {ts(t['time'])}  = {days:.1f} дней")
            entry = None
    if pos != 0 and entry is not None:
        days = (bars[-1].time - entry) / 86400
        print(f"  {ts(entry)} -> ОТКРЫТА до конца  = {days:.1f} дней")


if __name__ == "__main__":
    asyncio.run(main())
