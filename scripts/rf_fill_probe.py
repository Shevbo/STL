"""Сколько результата rich_fool держится на ОПТИМИСТИЧНОЙ наливке заявок.

Повод: у impulse_fade филл стоповой заявки по уровню вместо max(уровень, открытие)
превратил +148 929 / win 0.92 в −4 584 / win 0.54. У rich_fool уязвимость зеркальная:
вход фейда это ЛИМИТНАЯ заявка, и она наливалась, когда бар лишь КОСНУЛСЯ уровня
фитилём. При d_coef=0.05 уровни стоят в нескольких тиках от вчерашнего закрытия.

Гоняем ЛИДЕРА волны 2 на обоих кварталах RI, меняя только модель исполнения:
  fill_pen_pct — проход, который бар обязан сделать ЗА уровень для лимитного филла;
  slip_pct     — проскальзывание стоповых исполнений (вход пробоя и стоп-лосс).
Рядом считаем зеркало (invert=1): после честной наливки фейд обязан его обыгрывать,
иначе направление не доказано.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_fill_probe.py
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import json

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

WINDOWS = [("RIM6", "2026-03-20", "2026-06-17"), ("RIU6", "2026-06-19", "2026-09-09")]

# Лидер волны 2: худший квартал 529 874, net/MAE 8.8, 72-81 сделка
LEADER = dict(qty=1, d_coef=5, hold_min=20, n_days=5, step_count=8, vol_mult=25,
              sl_price_pct=50, trail_tp_pct=10, max_contracts=60,
              place_lead_min=10, ema_fast=9, ema_slow=21, exit_lead_min=120,
              allow_long=1, allow_short=1, bar_offset_min=0)

# (защита от проскальзывания В ПУНКТАХ, проскальзывание стопов в % от цены ×10000)
# 50 пунктов — указание оператора. Для RI это 5 тиков.
MODELS = [(0, 0), (10, 0), (25, 0), (50, 0), (100, 0), (50, 2), (50, 5)]


def load(secid: str, a: str, b: str) -> list[Bar]:
    rows = json.load(open(f"agent_bars/{secid}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(a).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(b).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows if lo <= r[0] <= hi]


async def run(mod, bars, secid, **over):
    p = {**LEADER, "symbol": secid, **over}
    r = await run_single_backtest(mod, bars, secid, p, point_value=1.0)
    mae = r["max_mae"] or 0
    return r["net_profit"], r["total_trades"], r["win_rate"], mae, (r["net_profit"] / mae if mae else 0)


async def main() -> None:
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    cache = {s: load(s, a, b) for s, a, b in WINDOWS}
    for secid, a, b in WINDOWS:
        bars = cache[secid]
        print(f"\n=== {secid}  {a}..{b}  ({len(bars)} баров) ===")
        print(f"{'защита':>8}{'слип':>6} | {'ФЕЙД net':>11}{'сд':>5}{'win':>6}{'net/MAE':>9}"
              f" | {'ПРОБОЙ net':>12}{'сд':>5} | вердикт зеркала")
        for pen, slip in MODELS:
            fn, ft, fw, fm, fr = await run(mod, bars, secid, invert=0,
                                           slip_guard_pts=pen, slip_pct=slip)
            bn, bt, _, _, _ = await run(mod, bars, secid, invert=1,
                                        slip_guard_pts=pen, slip_pct=slip)
            verdict = "фейд бьёт" if fn > bn else "ПРОБОЙ НЕ ХУЖЕ"
            print(f"{pen:>6.0f}пт{slip / 100:>5.2f}% | {fn:>11,.0f}{ft:>5}{fw:>6.2f}{fr:>9.1f}"
                  f" | {bn:>12,.0f}{bt:>5} | {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
