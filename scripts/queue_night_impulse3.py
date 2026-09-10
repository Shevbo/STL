"""Третья волна ночи: цель возврата × пауза, и режимные окна рост/падение.

ЗАЧЕМ ЦЕЛЬ×ПАУЗА. В волне 1 гонялась `mean_n` при закреплённой `cooldown`, в
волне 2 — `cooldown` при закреплённой `mean_n=120`. Ни одна не отвечает на вопрос,
СОВМЕСТНЫ ли они: у фейда цель возврата и пауза после сделки — это одна и та же
величина «сколько живёт импульс», разложенная на две оси, и оптимум по каждой в
отдельности не обязан быть оптимумом по паре.

ЗАЧЕМ РЕЖИМНЫЕ ОКНА. Живой конфиг зарабатывает на падении и теряет на росте
(«Направление, а не преимущество»), и сегодняшний бычий импульс вынес 50 тыс.
IN/OUT этого не различают: оба окна смешанные, и механизм, который просто стоит
в шорте, пройдёт их как «работающий». Здесь окна разделены по РЕЖИМУ, чтобы
утром было видно не «плюс/минус», а ГДЕ плюс:

  рост    2025-11-17..2026-01-25  (+19.9% по RI, зеркало текущего падения)
  падение 2026-05-04..2026-08-08  (−20.9%)

Фейд — механизм контртрендовый, у него нет причин любить одну сторону; если
плюс окажется только на падении, это направление, а не преимущество, и в реал
такое не идёт.

Приоритет 20 — ниже обеих предыдущих волн.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_night_impulse3.py --dry-run
    PYTHONPATH=. $PY scripts/queue_night_impulse3.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

# Длинные окна — для пары цель×пауза; режимные — для вопроса «где именно плюс».
WINDOWS = [
    ("in", "2025-04-01", "2025-12-31"),
    ("out", "2026-01-01", "2026-09-08"),
    ("up", "2025-11-17", "2026-01-25"),
    ("dn", "2026-05-04", "2026-08-08"),
]
SYMBOLS = ["RI", "Si", "GZ"]
CODE = "from trader.lab.strategies.impulse_fade import on_bar, on_start, on_stop"

AXES = {
    "mean_n": [15, 30, 60, 120, 240, 480],   # куда возвращается цена
    "cooldown": [0, 5, 20],                  # один импульс = одна сделка?
    "imp_bars": [2, 3, 5],
    "imp_atr": [15, 20, 30, 45],
    "take_atr": [8, 15, 25],
    "stop_atr": [20, 35],
    "max_hold": [30, 90],
}
PIN = dict(qty=1, atr_n=200, allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--windows", default="", help="только эти окна через запятую")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, v)) for v in itertools.product(*AXES.values())]
    wins = [w for w in WINDOWS
            if not args.windows or w[0] in args.windows.split(",")]

    jobs = []
    for sym in SYMBOLS:
        for win, d_from, d_to in wins:
            for inv in (0, 1):
                jobs.append({
                    "campaign": f"imf3-{sym.lower()}-{win}-i{inv}",
                    "scriptCode": CODE, "symbol": sym,
                    "baseParams": dict(PIN, symbol=sym, invert=inv),
                    "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                    "priority": 20,
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
