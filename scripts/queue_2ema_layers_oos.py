"""Вне выборки для слоёв DeskBot на Si-2EMA (e2l, 13.09.2026).

ЗАЧЕМ. На e2l Si (10,2100) со слоями тейк/трейл дал плюс на 6/6 кварталах 2025-2026.
Но база выбрана на тех же кварталах, а слой — после взгляда на базу. Проверка —
кварталы, которых не видел НИ ОДИН отбор: SiZ4 и SiH5 (сентябрь 2024 — март 2025).
Только отбраковка: два квартала подтвердить не могут. Соседние пары EMA не гоняем —
это было бы второе расширение сетки, закрытое коммитом e2b (нужно решение оператора).
Критерий и прогноз — в сообщении коммита, до запуска.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_2ema_layers_oos.py --dry-run
    PYTHONPATH=. $PY scripts/queue_2ema_layers_oos.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from scripts.queue_2ema import API, CODE, EMAIL, PIN
from scripts.queue_2ema_layers import layers
from trader.auth.portal import make_session_token

NEW_Q = [("SiZ4", "2024-09-20", "2024-12-19"), ("SiH5", "2024-12-20", "2025-03-19")]
KEEP = (0, 3, 5, 12)                      # база, тейк 1.2%, трейл 1/0.4, «как DeskBot»


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="e2o")
    ap.add_argument("--priority", type=int, default=20)
    args = ap.parse_args()

    sets = [lay for lay in layers("Si") if lay["layer_id"] in KEEP]
    body = [{
        "campaign": f"{args.tag}-{sym.lower()}", "scriptCode": CODE, "symbol": sym,
        "baseParams": dict(PIN, symbol=sym), "dateFrom": a, "dateTo": b,
        "engine": "remote", "priority": args.priority, "paramSets": sets,
    } for sym, a, b in NEW_Q]
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
