"""Вторая волна ночной очереди: закреплённые оси impulse_fade + четвёртый инструмент.

ЗАЧЕМ. Первая волна (`queue_night_impulse.py`) держит `cooldown` и `atr_n`
ЗАКРЕПЛЁННЫМИ — это не «неважные» оси, а непроверенные: cooldown решает, ловим мы
один импульс или серию входов в затухающее движение, а atr_n задаёт, по какой
памяти меряется «резко». Закреплённая ось, влияющая на частоту, это скрытая
подгонка, и без неё утренний вывод «механизм не работает» был бы неполным.

Вторая волна берёт ЛУЧШУЮ форму механизма (окно импульса и цель возврата из
дымового прогона: широкое окно + дальняя средняя дают знак стабильнее) и гоняет
по ней ровно эти две оси. Символы те же три: кэш `agent_bars` покрывает оба окна
целиком только по RI/Si/GZ (у SR он обрывается 19.06.2026), а задание, чьё окно
кэш не покрывает, уходит на ISS прямо с i9 и вешает ночь.

Приоритет 25: ниже первой волны, чтобы она отработала первой и утро имело
законченный ответ хотя бы по основной сетке.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_night_impulse2.py --dry-run
    PYTHONPATH=. $PY scripts/queue_night_impulse2.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

WINDOWS = [
    ("in", "2025-04-01", "2025-12-31"),
    ("out", "2026-01-01", "2026-09-08"),
]
SYMBOLS = ["RI", "Si", "GZ"]
CODE = "from trader.lab.strategies.impulse_fade import on_bar, on_start, on_stop"

# Оси, закреплённые в первой волне: обе управляют частотой, а частота — то, на чём
# умер rich_fool. Форма механизма зафиксирована по дымовому прогону.
AXES = {
    "cooldown": [0, 2, 5, 10, 20, 40],
    "atr_n": [60, 120, 200, 400, 800],
    "imp_bars": [3, 5],
    "imp_atr": [20, 30, 45],
    "take_atr": [15, 25],
    "stop_atr": [20, 35],
}
PIN = dict(qty=1, mean_n=120, max_hold=90, allow_long=1, allow_short=1,
           bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, v)) for v in itertools.product(*AXES.values())]

    jobs = []
    for sym in SYMBOLS:
        for win, d_from, d_to in WINDOWS:
            for inv in (0, 1):
                jobs.append({
                    "campaign": f"imf2-{sym.lower()}-{win}-i{inv}",
                    "scriptCode": CODE, "symbol": sym,
                    "baseParams": dict(PIN, symbol=sym, invert=inv),
                    "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                    "priority": 25,
                    "paramSets": combos,
                })

    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | комбо {total}")
    for j in jobs:
        print(f"  {j['campaign']:18s} {j['dateFrom']}..{j['dateTo']}  {len(j['paramSets'])} шт")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=180) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                ok += 1
            else:
                err += 1
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}, ошибок {err}, комбо {total}")


if __name__ == "__main__":
    main()
