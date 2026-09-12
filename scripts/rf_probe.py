"""Проба rich_fool на ЧИСТЫХ поконтрактных данных перед постановкой перебора.

Зачем именно поконтрактно: непрерывная серия RI сшита через перекат без выравнивания
базиса, шов 01.07.2026 составил −13.01%, и «лидер» ночного прогона сделал на нём 96%
итога. Внутри одного контракта швов нет (проверено: максимальный скачок 1.72%).

Смотрим три вещи:
  1. стратегия торгует, и сделок хватает для статистики;
  2. оси ЖИВЫЕ — разные параметры дают разные числа;
  3. ФЕЙД против ПРОБОЯ на ОДИНАКОВЫХ параметрах (matched pair).

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_probe.py
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import json

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

# Фронт-контракт и его ликвидное окно: от экспирации предыдущего до своей.
CONTRACTS = [
    ("RIM6", "2026-03-20", "2026-06-17"),
    ("RIU6", "2026-06-19", "2026-09-09"),
    ("SiM6", "2026-03-20", "2026-06-17"),
    ("SiU6", "2026-06-19", "2026-09-09"),
]

BASE = dict(qty=1, n_days=5, d_coef=100, step_count=3, vol_mult=13,
            max_contracts=60, sl_price_pct=150, trail_tp_pct=50,
            place_lead_min=10, hold_min=30,
            ema_fast=9, ema_slow=21, exit_lead_min=120,
            invert=0, allow_long=1, allow_short=1, bar_offset_min=0)


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
    return (r["net_profit"], r["total_trades"], r["win_rate"], mae,
            r["peak_contracts"], (r["net_profit"] / mae) if mae else 0.0)


async def main() -> None:
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    for secid, a, b in CONTRACTS:
        bars = load(secid, a, b)
        if len(bars) < 1000:
            print(f"\n=== {secid}: баров мало ({len(bars)}), пропускаю")
            continue
        days = len({datetime.datetime.fromtimestamp(x.time, datetime.timezone.utc).date()
                    for x in bars})
        print(f"\n=== {secid}  {a}..{b}:  {len(bars)} баров, {days} дней ===")
        print(f"{'вариант':<30}{'net':>11}{'сделок':>8}{'win':>6}{'MAE':>10}{'пик':>5}{'net/MAE':>9}")
        for label, over in [
            ("ФЕЙД базовый", {}),
            ("ПРОБОЙ (контроль)", dict(invert=1)),
            ("лестница узкая d=0.2", dict(d_coef=20)),
            ("лестница узкая d=0.5", dict(d_coef=50)),
            ("одна ступень", dict(step_count=1)),
            ("ступеней 8", dict(step_count=8)),
            ("объём ровный ×1.0", dict(vol_mult=10)),
            ("объём ×1.6", dict(vol_mult=16)),
            ("окно набора 120 мин", dict(hold_min=120)),
            ("без EMA-выхода", dict(exit_lead_min=0)),
        ]:
            net, tr, win, mae, peak, rmae = await one(mod, bars, secid, **over)
            print(f"{label:<30}{net:>11,.0f}{tr:>8}{win:>6.2f}{mae:>10,.0f}{peak:>5}{rmae:>9.1f}")


if __name__ == "__main__":
    asyncio.run(main())
