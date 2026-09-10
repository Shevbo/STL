"""Ночная очередь i9: impulse_fade (новый механизм) + добор supertrend по краю сетки.

ЗАЧЕМ. Вечерний прогон 10.09 занял 20 минут и оставил i9 простаивать. Здесь
очередь на 6-8 часов из двух частей:

1. IMPULSE_FADE — заказ оператора: резкий импульс за минуты на ВСЁМ торговом дне
   (не только предоткрытие, как в мёртвом rich_fool), вход против него, тейк на
   возврате к средней. Сетка крупная, потому что механизм новый и ни одна ось
   не «известна»: 5×5×4×3×3×3 = 2700 комбо на связку символ/окно/зеркало.

2. SUPERTREND-EDGE — в вечернем прогоне верх сел на КРАЙ сетки (multiplier=50 при
   потолке 50, atr_period=90 при потолке 90). Оптимум на краю не оптимум: сетка
   продлевается вправо, иначе мы просто не видели, где он на самом деле.

Гейт для кандидата (проверяется утром, не здесь): плюс на IN И на OUT, на обоих
символах, инвертированный близнец НЕ выигрывает, оптимум не на краю сетки.

  IN  2025-04-01..2025-12-31   OUT 2026-01-01..2026-09-08

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_night_impulse.py --dry-run
    PYTHONPATH=. $PY scripts/queue_night_impulse.py --submit
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
# Кэш agent_bars покрывает 01.04.2025..10.09.2026 по этим базовым кодам.
IMPULSE_SYMBOLS = ["RI", "Si", "GZ"]
SUPER_SYMBOLS = ["RI", "Si"]

IMPULSE_CODE = "from trader.lab.strategies.impulse_fade import on_bar, on_start, on_stop"
SUPER_CODE = "from trader.lab.strategies.supertrend import on_bar, on_start, on_stop"

IMPULSE_AXES = {
    "imp_bars": [1, 2, 3, 4, 5],        # окно импульса, минуты (<=5 по постановке)
    "imp_atr": [10, 15, 20, 30, 45],    # порог хода в десятых ATR
    "mean_n": [30, 60, 120, 240],       # куда возвращается цена
    "take_atr": [8, 15, 25],
    "stop_atr": [10, 20, 35],
    "max_hold": [10, 30, 90],
}
IMPULSE_PIN = dict(qty=1, atr_n=200, cooldown=5, allow_long=1, allow_short=1,
                   bar_offset_min=0)

# Верх вечернего прогона сидел на потолке обеих осей — продлеваем вправо.
SUPER_AXES = {
    "atr_period": [90, 120, 160, 210, 280, 360],
    "multiplier": [50, 65, 80, 100, 130, 170],
}
SUPER_PIN = dict(qty=1, allow_long=1, allow_short=1, bar_offset_min=0)


def combos(axes: dict) -> list[dict]:
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*axes.values())]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    imp = combos(IMPULSE_AXES)
    sup = combos(SUPER_AXES)
    jobs = []

    for sym in IMPULSE_SYMBOLS:
        for win, d_from, d_to in WINDOWS:
            for inv in (0, 1):
                jobs.append({
                    "campaign": f"imf-{sym.lower()}-{win}-i{inv}",
                    "scriptCode": IMPULSE_CODE, "symbol": sym,
                    "baseParams": dict(IMPULSE_PIN, symbol=sym, invert=inv),
                    "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                    "priority": 45,
                    "paramSets": imp,
                })
    for sym in SUPER_SYMBOLS:
        for win, d_from, d_to in WINDOWS:
            for inv in (0, 1):
                jobs.append({
                    "campaign": f"sup2-{sym.lower()}-{win}-i{inv}",
                    "scriptCode": SUPER_CODE, "symbol": sym,
                    "baseParams": dict(SUPER_PIN, symbol=sym, invert=inv),
                    "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                    "priority": 30,
                    "paramSets": sup,
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
