"""Перебор swing_trend (медленный тренд, покупка отката) на i9.

Зачем. Полгода перебора давали ноль, потому что всё было внутридневным (круг
съедал эдж) и с лестницей усреднения (мартингейл — скрытый хвост). Здесь класс
противоположный: низкая частота, фиксированная позиция qty=1, симметричный режим.

Сетка гонится СРАЗУ на двух окнах, чтобы к утру был честный ответ «живёт ли
победитель вне окна подгонки», без второго прогона:
  IN  2025-04-01..2025-12-31  — подгонка (9 месяцев)
  OUT 2026-01-01..2026-09-08  — вне выборки (8 месяцев, включая падение 2026)
Оба окна целиком внутри кэша agent_bars (01.04.2025..10.09.2026) — ISS не нужен.
Кандидат = верх на IN И плюс на OUT, на обоих символах, и его инвертированный
близнец при этом НЕ выигрывает (иначе знак режима ни при чём).

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_swing_trend.py --dry-run
    PYTHONPATH=. $PY scripts/queue_swing_trend.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
CODE = "from trader.lab.strategies.swing_trend import on_bar, on_start, on_stop"

SYMBOLS = ["RI", "Si"]
WINDOWS = [
    ("in",  "2025-04-01", "2025-12-31"),
    ("out", "2026-01-01", "2026-09-08"),
]
AXES = {
    "slow":     [2000, 4000, 8000, 12000],
    "fast":     [250, 500, 1000],
    "atr_n":    [100, 200, 500],
    "atr_mult": [2, 3, 4],
    "rr_x10":   [0, 20, 30],
}
PIN = dict(qty=1, allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*AXES.values())]
    jobs = []
    for sym in SYMBOLS:
        for win, d_from, d_to in WINDOWS:
            for inv in (0, 1):
                jobs.append({
                    "campaign": f"swingtrend-{sym}-{win}-inv{inv}",
                    "scriptCode": CODE, "symbol": sym,
                    "baseParams": dict(PIN, symbol=sym, invert=inv),
                    "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                    "priority": 40,
                    "paramSets": combos,
                })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"символы {SYMBOLS} | окна {len(WINDOWS)} | осей {len(keys)} | "
          f"заданий {len(jobs)} | комбо {total}")
    for j in jobs:
        print(f"  {j['campaign']:24s} {j['dateFrom']}..{j['dateTo']}  {len(j['paramSets'])} шт")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=120) as cl:
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
