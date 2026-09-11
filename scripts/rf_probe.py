"""Быстрая проба rich_fool на живых барах ПЕРЕД постановкой ночной очереди.

Смотрим ровно три вещи:
  1. стратегия вообще торгует (сделок хватает для статистики);
  2. ФЕЙД (invert=0) и ПРОБОЙ (invert=1) дают РАЗНЫЕ числа — значит ось работает;
  3. step_count влияет хотя бы при широком стопе — значит лестница набирает.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_probe.py
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


BASE = dict(qty=1, open_hour=10, open_min=0, place_lead_min=10, hold_min=120,
            n_days=10, step_count=3, vol_mult=15, max_contracts=120,
            sl_price_pct=220, trail_tp_pct=50,
            allow_long=1, allow_short=1, bar_offset_min=0)


async def main() -> None:
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    bars = load("RI")
    print(f"RI: {len(bars)} баров\n")
    print(f"{'вариант':<34} {'net':>11} {'сделок':>7} {'win':>5} {'пик':>5}")
    for label, over in [
        ("ФЕЙД базовый", {}),
        ("ПРОБОЙ (контроль, invert=1)", dict(invert=1)),
        ("ФЕЙД, 1 ступень", dict(step_count=1)),
        ("ФЕЙД, 8 ступеней", dict(step_count=8)),
        ("ФЕЙД, стоп 0.4%", dict(sl_price_pct=40)),
        ("ФЕЙД, стоп 6%", dict(sl_price_pct=600)),
        ("ФЕЙД, трейл 0.1%", dict(trail_tp_pct=10)),
        ("ФЕЙД, трейл 2.6%", dict(trail_tp_pct=260)),
    ]:
        p = {**BASE, "symbol": "RI", "invert": 0, **over}
        r = await run_single_backtest(mod, bars, "RI", p, point_value=1.0)
        print(f"{label:<34} {r['net_profit']:>11,.0f} {r['total_trades']:>7} "
              f"{r['win_rate']:>5.2f} {r['peak_contracts']:>5}")


if __name__ == "__main__":
    asyncio.run(main())
