#!/usr/bin/env python3
"""Растёт ли эдж на сделку, если замедлить осциллятор? Проверка за границей схемы.

Ночной перебор дал ровно четыре комбинации с ПОЛОЖИТЕЛЬНЫМ валовым сразу на двух
окнах, и все четыре — period=40 при узкой полосе (oversold 62 / overbought 38).
Сорок — это ПОТОЛОК params_schema, то есть оптимум упёрся в границу сетки, и
направление «медленнее» не проверено вовсе. Эдж там реальный (+0.10 ₽ на круг),
но комиссия круга на газе 4.70 ₽ — нужно в ~48 раз больше.

Единственный способ закрыть разрыв — брать за сделку БОЛЬШЕ, а не чаще. Поэтому
здесь period уходит далеко за 40 (движок читает params["period"] как есть, схема
ограничивает только UI и построители сеток). Вопрос один: растёт ли валовый НА
СДЕЛКУ с ростом периода, и если да — до каких величин.

Ответ «растёт и переваливает 4.70» = есть куда копать. «Болтается около нуля» =
у логики нет масштабируемого преимущества, и медленный вариант её не спасёт.

Запуск: cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
        nohup poetry run python -u scripts/wr_gz_slow.py > ~/wr_gz_slow.log 2>&1 &
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from trader.lab.commission import commission_for  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "wr", pathlib.Path(__file__).with_name("wr_gz_night.py"))
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)

# Далеко за схемный потолок 40: от «чуть медленнее» до «полтора дня на полупериод».
PERIODS = [40, 60, 90, 130, 200, 300, 450, 650, 900, 1300]
OVERSOLD = [58, 62, 66]
OVERBOUGHT = [34, 38, 42]
# Инструмент задаётся снаружи: тот же срез надо уметь прогнать на RI, где круг
# стоит 0.2 медианной минутки против 0.7 на газе.
SYMBOL = os.environ.get("SLOW_SYMBOL", "GZ")
FEE_SYM = os.environ.get("SLOW_FEE_SYMBOL", "GZU6")   # для группы сбора нужна серия
FEE_PRICE = float(os.environ.get("SLOW_PRICE", "9600"))
FEE_PV = float(os.environ.get("SLOW_PV", "1.0"))
FEE_ROUND = 2 * commission_for(FEE_SYM, FEE_PRICE, 1, FEE_PV, True)


def main() -> int:
    grid = [{"period": p, "oversold": o, "overbought": ob, "tp_atr": 0, "sl_pct": 0}
            for p in PERIODS for o in OVERSOLD for ob in OVERBOUGHT]
    wr.SYMBOL = SYMBOL                      # окна и сетка те же, инструмент другой
    wr.log(f"медленный срез {SYMBOL}: {len(grid)} комбинаций × {len(wr.WINDOWS)} окна, "
           f"порог безубытка {FEE_ROUND:.2f} ₽ за круг")
    client = httpx.Client(base_url=wr.API,
                          headers={"Authorization": f"Bearer {wr.token()}"})
    byw = {}
    for w, a, b in wr.WINDOWS:
        wr.log(f"окно {w} ({a}…{b})")
        byw[w] = wr.run_window(client, "s", w, a, b, grid)

    print("\nВаловый НА КРУГ по периоду (медиана по полосам), ₽:")
    print(f"{'period':>7} | " + " | ".join(f"{w:>10}" for w in byw)
          + " | сделок (медиана)")
    print("-" * 62)
    for p in PERIODS:
        cells, trs = [], []
        for w in byw:
            vals = [(v["net"] + v["trades"] * FEE_ROUND) / v["trades"]
                    for k, v in byw[w].items()
                    if k[0] == p and (v.get("trades") or 0) >= 30]
            t = [v["trades"] for k, v in byw[w].items() if k[0] == p and v.get("trades")]
            cells.append(f"{statistics.median(vals):+10.2f}" if vals else f"{'—':>10}")
            trs += t
        print(f"{p:>7} | " + " | ".join(cells)
              + f" | {int(statistics.median(trs)) if trs else 0}")
    print(f"\nПорог безубытка: {FEE_ROUND:.2f} ₽ за круг. Всё, что ниже, оборотом "
          f"не отбивается\nни при каких параметрах — это не подгонка, а арифметика.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
