"""Вне выборки для лидера bollinger_bo_m1 на золоте (вопрос оператора «кандидат в реал?», 14.09.2026).

ЛИДЕР. Отобран кампанией lbrbollinger1 на GDU6 03.05–07.08.2026 (+580k). Проверен на GDM6
(+697k, золото −12.9%, но май–июнь пересекается с окном отбора) и GDU6 19.06–10.09 (+132k,
+4.4%); зеркало __inv в минусе на обоих. Нетронутые кварталы GDH6, GDZ5, GDU5, GDM5 — все на
росте золота +9…+19%, то есть проверяют и эдж, и «не шортовая ли это машина».

ЧТО ГОНИМ. Параметры лидера дословно из лидерборда (строка stp GDM6 sl=0), символ подменяется;
sl_pct 0 и 50 (0.5%); база и __inv; flatten_end=1 (лестница до 7 контрактов — открытая
позиция на конце окна обязана попасть в net).
Критерий — в сообщении коммита, до запуска.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_gd_bollinger_oos.py --submit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg
import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
SRC = "camp-20260913-stpgdm6bollingerbom1"
QUARTERS = [("GDM5", "2025-03-20", "2025-06-19"), ("GDU5", "2025-06-20", "2025-09-18"),
            ("GDZ5", "2025-09-19", "2025-12-18"), ("GDH6", "2025-12-19", "2026-03-18")]


async def leader_params() -> dict:
    conn = await asyncpg.connect(os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"])
    try:
        row = await conn.fetchrow(
            "SELECT params FROM optimization_leaderboard WHERE campaign_run = $1 "
            "AND COALESCE((params->>'sl_pct')::float, 0) = 0 LIMIT 1", SRC)
    finally:
        await conn.close()
    p = row["params"] if isinstance(row["params"], dict) else json.loads(row["params"])
    return {k: v for k, v in p.items() if k != "symbol"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="gdo")
    ap.add_argument("--priority", type=int, default=20)
    args = ap.parse_args()

    p = asyncio.run(leader_params())
    print("лидер:", {k: v for k, v in p.items() if v not in (0, None, "")})
    sets = [{**p, "sl_pct": s, "flatten_end": 1} for s in (0, 50)]
    body = []
    for sid in ("bollinger_bo_m1", "bollinger_bo_m1__inv"):
        code = f"from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('{sid}')"
        for sym, a, b in QUARTERS:
            body.append({
                "campaign": f"{args.tag}{sym.lower()}{'inv' if sid.endswith('__inv') else 'base'}",
                "scriptCode": code, "symbol": sym, "baseParams": {"symbol": sym},
                "dateFrom": a, "dateTo": b, "engine": "remote", "priority": args.priority,
                "paramSets": sets,
            })
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
