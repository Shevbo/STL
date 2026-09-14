"""Конфиг оператора из TSLab (DeskBot 22.10, RI 1 мин) — в нашем движке (14.09.2026).

КОНФИГ (скрин оператора): 2EMA 61/123, тейк 3.1%, стоп 2.2% (от первого входа, как DeskBot),
тейк по RSI 80/80, трейл лонг активация 1.1% откат 1.1% (подтягивать 4.0), шорт 2.1% / 2.3%
(подтягивать 0.3), лестница позиции 1,1,32,32,32,32, шаг усреднения лонг 520 / шорт 260 пт,
лонг и шорт, ставки выкл. В TSLab: 582 сделки 05.01–11.09.2026, профит ~1.15 млн, весь плюс —
сделки на 32 лотах.

ВАРИАНТЫ (variant — ключ строки, движок его не читает):
  1  как в TSLab
  2  шортовый откат 0.3% вместо 2.3% («подтягивать» ближе отката — срабатывает ближний)
  3  без лестницы, 1 контракт — вклад сигнала и выходов
  4  без лестницы, 32 контракта — контроль РАВНОЙ экспозиции (урок e2o: лестница = плечо)
КВАРТАЛЫ: 2026 RIH6/RIM6/RIU6 — окно, на котором оператор оптимизировал; 2025 RIM5/RIU5/RIZ5 —
вне выборки. flatten_end=1: позиция на конце окна закрывается и попадает в net.
Спред в движке не моделируется (TSLab: спред 1, лимитные) — вычитать при чтении.

ЗАПУСК НА ХОСТЕРЕ (после update_token и сверки lib_sha):
    PYTHONPATH=. $PY scripts/queue_tslab_2ema.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from scripts.queue_2ema import API, CODE, EMAIL, PIN
from trader.auth.portal import make_session_token

QUARTERS = [
    ("RIH6", "2026-01-05", "2026-03-19"), ("RIM6", "2026-03-20", "2026-06-17"),
    ("RIU6", "2026-06-19", "2026-09-10"),
    ("RIM5", "2025-03-20", "2025-06-19"), ("RIU5", "2025-06-20", "2025-09-18"),
    ("RIZ5", "2025-09-19", "2025-12-18"),
]
TS = {"ema1": 61, "ema2": 123, "qty": 1, "bet_step": 0, "tp_pct": 310, "sl_pct": 220, "sl_first": 1,
      "rsi_tp_n": 80, "rsi_tp_lvl": 80, "trail_act_l": 110, "trail_back_l": 110,
      "trail_act_s": 210, "trail_back_s": 230, "avg_vols": "1,1,32,32,32,32",
      "avg_step_pts_l": 520, "avg_step_pts_s": 260, "flatten_end": 1}
VARIANTS = [
    {**TS, "variant": 1},
    {**TS, "variant": 2, "trail_back_s": 30},
    {**TS, "variant": 3, "avg_vols": "", "avg_step_pts_l": 0, "avg_step_pts_s": 0},
    {**TS, "variant": 4, "avg_vols": "", "avg_step_pts_l": 0, "avg_step_pts_s": 0, "qty": 32},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="tsl")
    ap.add_argument("--priority", type=int, default=20)
    args = ap.parse_args()

    body = [{
        "campaign": f"{args.tag}-{sym.lower()}", "scriptCode": CODE, "symbol": sym,
        "baseParams": dict(PIN, symbol=sym), "dateFrom": a, "dateTo": b,
        "engine": "remote", "priority": args.priority, "paramSets": VARIANTS,
    } for sym, a, b in QUARTERS]
    total = sum(len(j["paramSets"]) for j in body)
    print(f"заданий {len(body)} | комбо {total}")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=180) as cl:
        for j in body:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code >= 300:
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}/{len(body)}, комбо {total}")


if __name__ == "__main__":
    main()
