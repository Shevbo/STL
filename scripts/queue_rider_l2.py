"""Уровень 2 для победителей связки райдеров: второй контракт и другой период.

Всё, что мы пока знаем о райдерах, посчитано на ОДНОМ окне 04.05-08.08 и выбрано
как лучшее из сотен комбинаций на нём же. Это результат, но не кандидат: ровно на
этом шаге в тот же день рассыпались и bollinger_bo (весь плюс шортовый, второй
контракт валит четыре конфига из пяти), и triple_sma (прибылен только на своём
контракте в своём окне).

Три прогона на каждого победителя:
  СВОЙ КОНТРАКТ, своё окно  — контроль, при qty=1. Победители тянутся из БД как
                              есть, а базовый qty у строк из лидерборда бывает
                              и 19: тогда net в 19 раз больше не от стратегии.
  СОСЕДНИЙ КОНТРАКТ, то же окно — переносится ли преимущество на другой контракт
                              того же актива.
  СОСЕДНИЙ КОНТРАКТ, февраль-май — период, который в отборе не участвовал вовсе.

Расписание сторон и параметры эскалации едут вместе с конфигом: проверяем
победителя целиком, а не его половину.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_rider_l2.py --dry-run
    PYTHONPATH=. $PY scripts/queue_rider_l2.py --submit
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
IN_FROM, IN_TO = "2026-05-04", "2026-08-08"
OOS_FROM, OOS_TO = "2026-02-01", "2026-05-03"

# Соседний контракт того же актива. RIM6 и BRM6 живы в обоих окнах, поэтому
# годятся и как второй контракт, и как период вне отбора.
SIBLING = {"RIU6": "RIM6", "RIM6": "RIU6", "BRU6": "BRQ6", "BRQ6": "BRU6",
           "GDU6": "GDM6", "GDM6": "GDU6", "SiU6": "SiM6", "GZU6": "GZM6",
           "SRU6": "SRM6", "SVU6": "SVM6", "NGQ6": "NGM6", "MMU6": "MMM6"}
OOS_SYMBOL = {"RIU6": "RIM6", "RIM6": "RIM6", "BRU6": "BRM6", "BRQ6": "BRM6",
              "GDU6": "GDM6", "GDM6": "GDM6", "SiU6": "SiM6", "GZU6": "GZM6",
              "SRU6": "SRM6", "SVU6": "SVM6", "NGQ6": "NGM6", "MMU6": "MMM6"}


async def winners(dsn: str, prefix: str, top: int) -> list[tuple[str, str, str, dict]]:
    """Лучшие строки прогона: (тег, стратегия, символ, параметры).

    Ранжируем по recovery factor, а НЕ по net: райдеры наращивают позицию, и
    сортировка по деньгам ранжирует по размеру плеча. Одна строка на пару
    (стратегия, символ) — соседние строки одного конфига это не разные гипотезы.
    """
    c = await asyncpg.connect(dsn)
    try:
        rows = await c.fetch(
            "SELECT r.id, r.strategy, r.symbol, b.params, b.net_profit, "
            "       b.peak_contracts, b.recovery_factor "
            "  FROM backtest_results b JOIN backtest_runs r ON r.id = b.run_id "
            " WHERE r.id LIKE $1 AND b.net_profit > 0 AND b.total_trades >= 150 "
            " ORDER BY b.recovery_factor DESC NULLS LAST", prefix)
    finally:
        await c.close()
    out: dict[tuple, tuple] = {}
    for r in rows:
        key = (r["strategy"], r["symbol"])
        if key in out:
            continue
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else dict(p)
        tag = f"{r['strategy'][:9]}{r['symbol'][:4]}".lower().replace("_", "")
        out[key] = (tag, r["strategy"], r["symbol"], p)
        if len(out) >= top:
            break
    return list(out.values())


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="camp-20260814-combo%",
                    help="LIKE-шаблон прогона, чьих победителей проверяем")
    ap.add_argument("--top", type=int, default=4, help="сколько пар стратегия+символ брать")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    won = await winners(dsn, args.prefix, args.top)
    if not won:
        raise SystemExit(f"победителей не найдено по шаблону {args.prefix!r}")

    jobs = []
    for tag, sid, sym, p in won:
        p = dict(p)
        was_qty = int(p.get("qty", 1) or 1)
        p["qty"] = 1                     # qty только масштабирует P&L
        p.pop("symbol", None)
        code = (f"from trader.lab.strategies.library import make_on_bar\n"
                f"on_bar = make_on_bar('{sid}')")
        plan = [(f"{tag}own", sym, IN_FROM, IN_TO),
                (f"{tag}sib", SIBLING.get(sym, sym), IN_FROM, IN_TO),
                (f"{tag}oos", OOS_SYMBOL.get(sym, sym), OOS_FROM, OOS_TO)]
        for name, s, a, b in plan:
            jobs.append({"campaign": name, "scriptCode": code, "symbol": s,
                         "baseParams": dict(p, symbol=s), "dateFrom": a, "dateTo": b,
                         "engine": "remote", "priority": 100,
                         "paramSets": [{}]})
        print(f"  {tag:12s} {sid:16s} {sym} -> {SIBLING.get(sym)} / "
              f"{OOS_SYMBOL.get(sym)}  (qty {was_qty} -> 1)")

    print(f"прогонов {len(jobs)}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code not in (200, 201, 202):
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok} из {len(jobs)}")


if __name__ == "__main__":
    asyncio.run(main())
