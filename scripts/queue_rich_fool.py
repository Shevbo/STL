"""Перебор стратегии rich_fool (предоткрытийная лестница пробоя) на i9.

Гипотеза оператора 09.09.2026, ФОРМУЛА v2 (после правки алгоритма):
    price(0) = prev_close, price(n) = price(n-1) + D/(n+1), D = amp (амплитуда за n_days).
Зазоры убывают: D/2, D/3, D/4, … Объём ступени растёт ×vol_mult (10 = 1.0 = ровно qty).

Оси: n_days (амплитуда), step_count (число ступеней), vol_mult (рост объёма),
sl_pct (риск R = amp·sl_pct), rr_x10 (тейк:стоп), invert (пробой/фейд),
open_hour (07:00 утро vs 10:00 осн. сессия). Контракты RI + Si, 6 месяцев.

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
    "n_days":     [2, 3, 5, 8, 12, 20, 30],
    "step_count": [2, 3, 5, 8, 12, 20],
    "vol_mult":   [10, 15, 20, 30, 50],
    # TP/SL — широко, по прямому запросу оператора. R = amp·sl_pct (15% амплитуды —
    # тесный стоп, 80% — «пусть дышит»), тейк = rr·R (1.0 — снять сразу, 6.0 — везти).
    "sl_pct":     [15, 20, 30, 45, 60, 80],
    "rr_x10":     [10, 15, 20, 30, 40, 60],
    "invert":     [0, 1],
    "open_hour":  [7, 10],
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
    # Чанк по open_hour + invert + n_days: после расширения осей TP/SL один чанк
    # «символ×час×invert» разросся бы до ~7.5k paramSets в одном задании.
    fixed = ("open_hour", "invert", "n_days")
    for sym in SYMBOLS:
        for oh in AXES["open_hour"]:
            for inv in AXES["invert"]:
                for nd in AXES["n_days"]:
                    sets = [c for c in combos if c["open_hour"] == oh
                            and c["invert"] == inv and c["n_days"] == nd]
                    jobs.append({
                        "campaign": f"richfool-{sym}-oh{oh}-inv{inv}-nd{nd}",
                        "scriptCode": CODE, "symbol": sym,
                        "baseParams": dict(PIN, symbol=sym, open_hour=oh, invert=inv, n_days=nd),
                        "dateFrom": D_FROM, "dateTo": D_TO, "engine": "remote",
                        "priority": 40,    # прямой запрос оператора — вперёд фоновых кампаний
                        "paramSets": [{k: c[k] for k in keys if k not in fixed} for c in sets],
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
