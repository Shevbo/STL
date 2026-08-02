#!/usr/bin/env python3
"""Стена комиссии: сколько минутных движений стоит один круг сделки, по инструментам.

Зачем. Williams %R на газе оказался убыточен ДО комиссии, но главная причина глубже:
на M1 круг сделки на GZ стоит ~4.7 ₽ при цене пункта 1 ₽, то есть 4.7 пункта — а
медианная минутка газа проходит заметно меньше. Стратегия физически не может
отбить оборот. Прежде чем перебирать параметры на новом инструменте, полезно
знать, сколько минуток надо «поймать» только чтобы выйти в ноль.

Считает по УЖЕ закэшированным барам (agent_bars/*.json), без сети и без i9:
  порог = (2 × тейкерский сбор + ОДИН ШАГ ЦЕНЫ) / цена пункта   (в пунктах на круг)
  медианный размах минутки = median(high - low)                 (в пунктах)
  ЦЕНА ОБОРОТА = порог / размах                        (сколько минуток в одном круге)

Шаг цены входит в порог, потому что бэктест исполняет по открытию следующего бара
и спред НЕ моделирует, а робот на агенте кроссит спред и теряет за круг минимум
один шаг. Для RI это 40% истинной стоимости круга (16 руб шага против 24 руб
сбора) — без этой поправки индекс выглядел вдвое привлекательнее, чем есть.
Один шаг — это ПОЛ, а не оценка: в тонкой книге спред бывает шире.

Чем больше ЦЕНА ОБОРОТА, тем безнадёжнее высокочастотная логика на этом инструменте.

Запуск: cd ~/apps/shectory-trader && poetry run python scripts/fee_wall.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.lab.commission import commission_for, fee_group  # noqa: E402
from trader.lab.iss_loader import fetch_contract_spec  # noqa: E402

BARS_DIR = "agent_bars"
MIN_BARS = 5000          # меньше — статистика размаха ненадёжна


async def main() -> int:
    files = sorted(f for f in os.listdir(BARS_DIR) if f.endswith(".json"))
    rows = []
    for fn in files:
        sym = fn[:-5]
        try:
            rec = json.load(open(os.path.join(BARS_DIR, fn), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        bars = rec.get("rows") or []
        if len(bars) < MIN_BARS:
            continue
        # Берём хвост: свежие данные, актуальная цена и волатильность.
        tail = bars[-40000:]
        rng = [b[2] - b[3] for b in tail if b[2] is not None and b[3] is not None]
        rng = [x for x in rng if x > 0]
        if len(rng) < MIN_BARS:
            continue
        price = statistics.median(b[4] for b in tail)
        spec = await fetch_contract_spec(sym)
        pv = (spec or {}).get("point_value")
        if not pv:
            continue
        fee_rub = 2 * commission_for(sym, price, 1, pv, True)
        tick_rub = float((spec or {}).get("step_price") or 0)   # один шаг цены = спред
        fee_rub += tick_rub
        fee_pts = fee_rub / pv
        med = statistics.median(rng)
        rows.append({
            "sym": sym, "group": fee_group(sym), "price": price, "pv": pv,
            "fee_rub": fee_rub, "tick_rub": tick_rub, "fee_pts": fee_pts,
            "med_range": med, "cost": fee_pts / med,
        })
        await asyncio.sleep(0.2)          # вежливо к ISS

    rows.sort(key=lambda r: r["cost"])
    print(f"{'инстр':8} {'группа':10} {'₽/пункт':>9} {'круг, ₽':>9} {'в т.ч. шаг':>11} "
          f"{'круг, пт':>9} {'размах M1':>10} {'ЦЕНА ОБОРОТА':>13}")
    print("-" * 88)
    for r in rows:
        print(f"{r['sym']:8} {r['group']:10} {r['pv']:9.3f} {r['fee_rub']:9.2f} "
              f"{r['tick_rub']:11.2f} {r['fee_pts']:9.2f} {r['med_range']:10.2f} "
              f"{r['cost']:12.1f}×")
    print("\nЦЕНА ОБОРОТА = сколько медианных минуток надо пройти в свою сторону,")
    print("чтобы круг сделки вышел в ноль. Для M1-логики всё, что сильно выше 1×,")
    print("означает: отбивать оборот придётся многобарным движением, а не шумом.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
