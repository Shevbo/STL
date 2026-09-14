"""Конфиг TSLab «2→64» оператора, расшифрованный по его журналу сделок (14.09.2026).

РАСШИФРОВКА (журнал 573 сделок, 05.01–11.09.2026, итог TSLab 1.40 млн в пунктах):
  стоп от первого входа по сторонам: лонг 1.1%, шорт 2.1% (все LX ровно −1.10%, SX-минус +2.10%);
  трейл: шорт активация 2.3% откат 0.3%; лонг 1.1% / 4.0% (фактически не срабатывает);
  тейк 3.1% от средней; RSI-тейк 80/80; 2EMA 61/123;
  лестница позиции 2,4,8,16,32,64; шаг лонг 200, шорт 250 пунктов.
ВАРИАНТЫ (variant — ключ строки):
  1  расшифровка как есть
  2  без лестницы, 2 лота — вклад сигнала и выходов
  3  без лестницы, 64 лота — контроль равной экспозиции
КВАРТАЛЫ: 2026 RIH6/RIM6/RIU6 — окно оптимизации TSLab; 2025 RIM5/RIU5/RIZ5 — вне выборки.
flatten_end=1. Истёкшие контракты — в пунктах (point_value=1), RIU6 — в рублях.

ЗАПУСК НА ХОСТЕРЕ (после update_token и сверки lib_sha):
    PYTHONPATH=. $PY scripts/queue_tslab64.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from scripts.queue_2ema import API, CODE, EMAIL, PIN
from scripts.queue_tslab_2ema import QUARTERS
from trader.auth.portal import make_session_token

TS = {"ema1": 61, "ema2": 123, "qty": 2, "bet_step": 0, "tp_pct": 310, "sl_first": 1,
      "sl_pct_l": 110, "sl_pct_s": 210, "trail_act_l": 110, "trail_back_l": 400,
      "trail_act_s": 230, "trail_back_s": 30, "rsi_tp_n": 80, "rsi_tp_lvl": 80,
      "avg_vols": "2,4,8,16,32,64", "avg_step_pts_l": 200, "avg_step_pts_s": 250,
      "flatten_end": 1}
VARIANTS = [
    {**TS, "variant": 1},
    {**TS, "variant": 2, "avg_vols": "", "avg_step_pts_l": 0, "avg_step_pts_s": 0},
    {**TS, "variant": 3, "avg_vols": "", "avg_step_pts_l": 0, "avg_step_pts_s": 0, "qty": 64},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="t64")
    ap.add_argument("--priority", type=int, default=20)
    # Подсказка DeskBot (оператор 14.09.2026): вход только на ПЕРЕСЕЧЕНИИ EMA. --cross: варианты
    # 1-2 с cross_only=1 как 4-5 (тег t6c). Проверка репликации: позиций за 2026 ~573.
    ap.add_argument("--cross", action="store_true")
    args = ap.parse_args()

    variants = ([{**v, "variant": v["variant"] + 3, "cross_only": 1} for v in VARIANTS[:2]]
                if args.cross else VARIANTS)
    body = [{
        "campaign": f"{args.tag}-{sym.lower()}", "scriptCode": CODE, "symbol": sym,
        "baseParams": dict(PIN, symbol=sym), "dateFrom": a, "dateTo": b,
        "engine": "remote", "priority": args.priority, "paramSets": variants,
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
