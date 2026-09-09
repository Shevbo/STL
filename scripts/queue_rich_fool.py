"""Перебор стратегии rich_fool (предоткрытийная лестница пробоя) на i9.

Гипотеза оператора 09.09.2026. Оси: n_days (амплитуда), dist_pct (отдаление 1-й
ступени), step_count, step_gap_pct, sl_pct (риск R), rr_x10 (тейк:стоп),
invert (пробой/фейд), open_hour (07:00 утро vs 10:00 осн. сессия — пусть данные
решат). Контракты RI + Si непрерывной склейкой, 6 месяцев.

BR исключён: непрерывный загрузчик отдаёт для BR один день на запрошенный период,
и BR-сессия открывается ~09:00, не 07:00 — отдельный разбор, не здесь.

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_rich_fool.py --dry-run
    PYTHONPATH=. $PY scripts/queue_rich_fool.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
D_FROM, D_TO = "2026-03-09", "2026-09-09"
CODE = ("from trader.lab.strategies.rich_fool import on_bar, on_start, on_stop")

SYMBOLS = ["RI", "Si"]
AXES = {
    "n_days":       [3, 5, 8],
    "dist_pct":     [10, 20, 30, 45, 65],
    "step_count":   [1, 2, 3],
    "step_gap_pct": [15, 30],
    "sl_pct":       [20, 30, 45],
    "rr_x10":       [15, 20, 30],
    "invert":       [0, 1],
    "open_hour":    [7, 10],
}
PIN = dict(qty=1, open_min=0, place_lead_min=10, hold_min=30,
           allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*AXES.values())]
    jobs = []
    for sym in SYMBOLS:
        # чанк по open_hour + invert, чтобы paramSets в одном задании не разрастался
        for oh in AXES["open_hour"]:
            for inv in AXES["invert"]:
                sets = [c for c in combos if c["open_hour"] == oh and c["invert"] == inv]
                jobs.append({
                    "campaign": f"richfool-{sym}-oh{oh}-inv{inv}",
                    "scriptCode": CODE, "symbol": sym,
                    "baseParams": dict(PIN, symbol=sym, open_hour=oh, invert=inv),
                    "dateFrom": D_FROM, "dateTo": D_TO, "engine": "remote",
                    "priority": 40,        # прямой запрос оператора — вперёд фоновых кампаний
                    "paramSets": [{k: c[k] for k in keys if k not in ("open_hour", "invert")}
                                 for c in sets],
                })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"символы {SYMBOLS} | заданий {len(jobs)} | комбо {total}")
    for j in jobs[:4]:
        print(f"  {j['campaign']:26s} sets={len(j['paramSets'])}")
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
