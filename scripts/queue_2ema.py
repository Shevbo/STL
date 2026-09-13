"""Перебор shectory_2ema на M1: голый кросс EMA, без украшений (заказ оператора 13.09.2026).

ЗАЧЕМ. Оператор принёс настройку DeskBot 2EMA (EMA 10/140 на M15, тейк/стоп в %, трейлинг,
тейк по RSI, лестница объёмов). Повторять её не надо — берём наш реестровый 2EMA на
минутках и прокачиваем идеями. Прежде чем вешать слои, мерим сам сигнал: если в голом
кроссе на M1 нет знака, который держится между кварталами, тейки и трейлинги поверх —
подгонка.

ОСИ. ema1 × ema2. Длинные ema2 (1200, 2100) — это M15 DeskBot в минутах: 140×15 = 2100.
ema1 >= ema2 выкинуты (вырождение, см. sig_2ema). Ставки выключены (bet_step=0), усреднения
нет — чистый переворот по кроссу, qty=1.

КВАРТАЛЫ. Те же, что у valley_spike (vs_gate): выборка M6/U6, вне M5/U5/Z5/H6 по RI и Si.

ПЕРЕД ЗАПУСКОМ: сверить lib_sha в i9_heartbeat с library.py на ХОСТЕРЕ.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_2ema.py --dry-run
    PYTHONPATH=. $PY scripts/queue_2ema.py --submit
ГЕЙТ:
    PYTHONPATH=. $PY scripts/vs_gate.py --strategy shectory_2ema --keys ema1,ema2 --tag e2a
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
CODE = "from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('shectory_2ema')"

CONTRACTS = [
    (f"{inst}{q}", a, b) for inst in ("RI", "Si") for q, a, b in (
        ("M6", "2026-03-20", "2026-06-17"),
        ("U6", "2026-06-19", "2026-09-10"),
        ("M5", "2025-03-20", "2025-06-19"),
        ("U5", "2025-06-20", "2025-09-18"),
        ("Z5", "2025-09-19", "2025-12-18"),
        ("H6", "2025-12-19", "2026-03-19"),
    )
]

EMA1 = [10, 30, 60, 150]
EMA2 = [140, 300, 600, 1200, 2100]
PIN = dict(qty=1, bet_step=0, bet_max=10, avg_max=1, avg_step_atr=0, tp_atr=0,
           sl_frac=0, sl_pct=0, allow_long=1, allow_short=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="e2a")
    ap.add_argument("--priority", type=int, default=20)
    # Сетка из командной строки: e2b дотягивает край Si (10, 2100) — ema1 ниже, ema2 выше.
    ap.add_argument("--ema1", default=",".join(map(str, EMA1)))
    ap.add_argument("--ema2", default=",".join(map(str, EMA2)))
    args = ap.parse_args()

    e1 = [int(x) for x in args.ema1.split(",")]
    e2 = [int(x) for x in args.ema2.split(",")]
    combos = [{"ema1": f, "ema2": s} for f, s in itertools.product(e1, e2) if f < s]
    jobs = [{
        "campaign": f"{args.tag}-{sym.lower()}",
        "scriptCode": CODE, "symbol": sym,
        "baseParams": dict(PIN, symbol=sym),
        "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
        "priority": args.priority,
        "paramSets": combos,
    } for sym, d_from, d_to in CONTRACTS]

    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | комбо {total}")
    for j in jobs:
        print(f"  {j['campaign']:12s} {j['dateFrom']}..{j['dateTo']}  {len(j['paramSets'])} шт")
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
