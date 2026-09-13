"""Вне выборки для лидеров macd_shectory1 «5 из 5 окон RI» (запрос оператора 13.09.2026).

ЗАЧЕМ. В лидерборде за всё время лучшие по устойчивости строки — macd_shectory1 с плюсом
на пяти окнах RI (RIZ5, RIH6, RIM6, RIU6 ×2, кампании 15-17.08.2026). Но все пять окон
участвовали в отборе из тысяч комбинаций, окна пересекаются, а по сути это два режима
(рост ноя-янв и падение фев-авг). Проверки вне выборки не было.

ЧТО ДЕЛАЕТ. Берёт из optimization_leaderboard верхние --top конфигов по срезу «одни
параметры на >=4 окнах и >=2 контрактах» (тот же порядок, что в отчёте) и гонит их
ДОСЛОВНО на окнах, которых отбор не видел:
  RIM5 20.03-19.06.2025, RIU5 20.06-18.09.2025, RIZ5 19.09-15.11.2025 (до окна отбора),
  RIU6 17.08-10.09.2026 (после).

КРИТЕРИЙ (зафиксирован до запуска, в сообщении коммита).

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_macd_oos.py --dry-run
    PYTHONPATH=. $PY scripts/queue_macd_oos.py --submit
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
CODE = "from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('macd_shectory1')"
WINDOWS = [
    ("RIM5", "2025-03-20", "2025-06-19"),
    ("RIU5", "2025-06-20", "2025-09-18"),
    ("RIZ5", "2025-09-19", "2025-11-15"),
    ("RIU6", "2026-08-17", "2026-09-10"),
]
TOP = """WITH w AS (
  SELECT (params - 'symbol') p, symbol, date_from, date_to, max(net_profit) net
  FROM optimization_leaderboard
  WHERE strategy = 'macd_shectory1' AND left(symbol, 2) = 'RI' AND date_from IS NOT NULL AND total_trades >= 30
  GROUP BY 1, 2, 3, 4)
 SELECT p, count(*) wt, sum((net > 0)::int) wp, sum(net) s FROM w GROUP BY p
 HAVING count(*) >= 4 AND count(DISTINCT symbol) >= 2
 ORDER BY sum((net > 0)::int)::float / count(*) DESC, count(*) DESC, sum(net) DESC LIMIT $1"""


async def top_configs(n: int) -> list[dict]:
    conn = await asyncpg.connect(os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"])
    try:
        await conn.execute("SET statement_timeout='900s'")
        rows = await conn.fetch(TOP, n)
    finally:
        await conn.close()
    out = []
    for r in rows:
        p = r["p"] if isinstance(r["p"], dict) else json.loads(r["p"])
        out.append(p)
        print(f"  {r['wp']}/{r['wt']} sum={r['s']:.0f} fast={p.get('fast')} slow={p.get('slow')} "
              f"signal={p.get('signal')} allow_long={p.get('allow_long')} allow_short={p.get('allow_short')}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="mo1")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--priority", type=int, default=20)
    args = ap.parse_args()

    combos = asyncio.run(top_configs(args.top))
    jobs = [{
        "campaign": f"{args.tag}-{sym.lower()}",
        "scriptCode": CODE, "symbol": sym,
        "baseParams": {"symbol": sym},
        "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
        "priority": args.priority,
        "paramSets": combos,
    } for sym, d_from, d_to in WINDOWS]
    print(f"заданий {len(jobs)} | комбо {sum(len(j['paramSets']) for j in jobs)}")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=180) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            print(f"  {j['campaign']}: {r.status_code} {'' if r.status_code < 300 else r.text[:200]}")


if __name__ == "__main__":
    main()
