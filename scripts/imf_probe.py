"""Проба impulse_fade на поконтрактных данных ПЕРЕД постановкой перебора.

rich_fool умер не от параметров, а от частоты: 0 из 2430 строк дали >=150 сделок
за полгода. Здесь заявка висит ещё дальше от цены, поэтому первый вопрос тот же и
он один: СКОЛЬКО СДЕЛОК даёт дистанция. Если на ликвидном квартале их единицы —
перебор ставить незачем, сколько бы ни был хорош net.

Смотрим три вещи:
  1. частота по оси lvl_atr — на каком расстоянии прокол ещё случается;
  2. ось ЖИВАЯ: разные lvl_atr дают разные числа (совпавшие net И число сделок =
     параметр не дошёл до стратегии, ловушка мёртвой оси);
  3. гейт боковика flat_only=0/1 на ОДИНАКОВЫХ параметрах — matched pair.

P&L в ПУНКТАХ (point_value=1.0), как у rf_probe: сравниваем механику, не рубли.

ЗАПУСК НА ХОСТЕРЕ: cd ~/apps/shectory-trader && PYTHONPATH=. $PY scripts/imf_probe.py
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import json

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

CONTRACTS = [
    ("RIM6", "2026-03-20", "2026-06-17"),
    ("RIU6", "2026-06-19", "2026-09-09"),
    ("SiM6", "2026-03-20", "2026-06-17"),
    ("SiU6", "2026-06-19", "2026-09-09"),
]

BASE = dict(qty=1, mean_n=60, atr_n=200, lvl_atr=60, step_count=1, step_atr=20,
            imp_bars=5, imp_frac=50, ret_pct=50, stop_atr=20, max_hold=120,
            cooldown=5, flat_only=1, reg_win=14400, reg_drift=300,
            invert=0, allow_long=1, allow_short=1, bar_offset_min=0)

LVL = [40, 60, 90, 140, 200]


def load(secid: str, d_from: str, d_to: str) -> list[Bar]:
    rows = json.load(open(f"agent_bars/{secid}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(d_from).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(d_to).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows if lo <= r[0] <= hi]


async def one(mod, bars, secid, **over):
    p = {**BASE, "symbol": secid, **over}
    r = await run_single_backtest(mod, bars, secid, p, point_value=1.0)
    mae = r["max_mae"] or 0
    return (r["net_profit"], r["total_trades"], r["win_rate"], mae)


async def main() -> None:
    mod = importlib.import_module("trader.lab.strategies.impulse_fade")
    for secid, a, b in CONTRACTS:
        bars = load(secid, a, b)
        if len(bars) < 1000:
            print(f"\n=== {secid}: баров мало ({len(bars)}), пропускаю")
            continue
        print(f"\n=== {secid} {a}..{b} | баров {len(bars)}")
        print(f"{'lvl_atr':>8} {'flat':>5} {'сделок':>7} {'net, пт':>12} {'win%':>6} {'MAE':>10}")
        for lvl in LVL:
            for flat in (1, 0):
                net, tr, win, mae = await one(mod, bars, secid, lvl_atr=lvl, flat_only=flat)
                print(f"{lvl:8d} {flat:5d} {tr:7d} {net:12.0f} {win:6.1f} {mae:10.0f}")


if __name__ == "__main__":
    asyncio.run(main())
