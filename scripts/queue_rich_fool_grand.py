"""Большой ночной перебор rich_fool: все параметры, расширенные диапазоны.

Запрос оператора 09.09.2026: step_count до 25, n_days до 30, dist до 100%
амплитуды, нелинейная расстановка (spacing/span_pct), нарастающий объём (vol_mult).
max_contracts=120 — жёсткий потолок против vol_mult^step_count.

~61k комбо, RI+Si, полгода. priority 40 (вперёд фоновой costcand). Чанк по
(symbol, open_hour, spacing, vol_mult), чтобы paramSets в задании не разрастался.

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_rich_fool_grand.py --dry-run
    PYTHONPATH=. $PY scripts/queue_rich_fool_grand.py --submit
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
CODE = "from trader.lab.strategies.rich_fool import on_bar, on_start, on_stop"
SYMBOLS = ["RI", "Si"]

AXES = {
    "n_days":     [5, 10, 20, 30],
    "dist_pct":   [4, 10, 25, 60],
    "step_count": [2, 4, 8, 15, 25],
    "spacing":    [0, 1, 2],
    "span_pct":   [120, 250],
    "sl_pct":     [20, 35],
    "rr_x10":     [15, 25],
    "vol_mult":   [10, 15],
    "hold_min":   [30, 90],
    "invert":     [0, 1],
    "open_hour":  [7, 10],
}
CHUNK = ("open_hour", "spacing", "vol_mult")     # уходят в baseParams, не в paramSets
PIN = dict(qty=1, step_gap_pct=15, max_contracts=120, open_min=0,
           place_lead_min=10, allow_long=1, allow_short=1, bar_offset_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    keys = list(AXES)
    combos = [dict(zip(keys, v)) for v in itertools.product(*AXES.values())]
    set_keys = [k for k in keys if k not in CHUNK]
    jobs = []
    for sym in SYMBOLS:
        for oh, spc, vm in itertools.product(AXES["open_hour"], AXES["spacing"], AXES["vol_mult"]):
            sets = [c for c in combos
                    if c["open_hour"] == oh and c["spacing"] == spc and c["vol_mult"] == vm]
            jobs.append({
                "campaign": f"rfgrand-{sym}-oh{oh}-sp{spc}-vm{vm}",
                "scriptCode": CODE, "symbol": sym,
                "baseParams": dict(PIN, symbol=sym, open_hour=oh, spacing=spc, vol_mult=vm),
                "dateFrom": D_FROM, "dateTo": D_TO, "engine": "remote", "priority": 40,
                "paramSets": [{k: c[k] for k in set_keys} for c in sets],
            })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"символы {SYMBOLS} | заданий {len(jobs)} | комбо {total} | сеты/задание ~{total // len(jobs)}")
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
