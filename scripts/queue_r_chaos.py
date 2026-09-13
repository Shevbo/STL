"""Хаос R на лидерах всех стратегий реестра (заказ оператора 13.09.2026).

ИДЕЯ ОПЕРАТОРА. Каждая R-я сделка делается инверсной — вносит хаос в ожидания того, кто
читает поток заявок. Параметр r_inv_every в make_on_bar.

ЛИДЕРЫ. По каждой стратегии реестра (make_on_bar) — строка лидерборда с максимальным net
среди: >=100 сделок, контракт M6 или U6, посчитана после 06.08.2026 (починка заморозки
MACD). Параметры дословно, символ подменяется. Лидеры выбраны В ВЫБОРКЕ — судим не
абсолютный net, а РАЗНИЦУ R против R=0 на том же конфиге и квартале.

КВАРТАЛЫ. Два: M6 20.03-17.06.2026 и U6 19.06-10.09.2026 того инструмента, на котором лидер.
R: 0 (контроль), 2, 3, 5, 10.

НЕ ВХОДЯТ: rich_fool, donchian_breakout, supertrend, impulse_fade и прочие отдельные
модули — у них своя механика входов, R в них не встроен.

ЗАПУСК НА ХОСТЕРЕ (после update_token и сверки lib_sha):
    PYTHONPATH=. $PY scripts/queue_r_chaos.py --dry-run
    PYTHONPATH=. $PY scripts/queue_r_chaos.py --submit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg
import httpx

from trader.auth.portal import make_session_token
from trader.lab.strategies.library import REGISTRY

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
QUARTERS = {"M6": ("2026-03-20", "2026-06-17"), "U6": ("2026-06-19", "2026-09-10")}
RS = (0, 2, 3, 5, 10)
LEADERS = """SELECT DISTINCT ON (strategy) strategy, symbol, net_profit, total_trades, params
 FROM optimization_leaderboard
 WHERE strategy = ANY($1::text[]) AND total_trades >= 100 AND created_at >= '2026-08-06'
   AND symbol ~ '(M6|U6)$'
 ORDER BY strategy, net_profit DESC"""


async def leaders() -> list[dict]:
    ids = [k for k in REGISTRY if not k.startswith("_")]
    conn = await asyncpg.connect(os.environ.get("LAB_DB_URL") or os.environ["DATABASE_URL"])
    try:
        await conn.execute("SET statement_timeout='1200s'")
        rows = await conn.fetch(LEADERS, ids)
    finally:
        await conn.close()
    out = []
    for r in rows:
        p = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"])
        p = {k: v for k, v in p.items() if k not in ("symbol", "r_inv_every")}
        out.append({"strategy": r["strategy"], "base": r["symbol"][:-2], "params": p})
        print(f"  {r['strategy']:22s} {r['symbol']:6s} net={r['net_profit']:.0f} tr={r['total_trades']}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--tag", default="rch")
    ap.add_argument("--priority", type=int, default=20)
    # Серая зона стопов (оператор 13.09.2026): у __inv и вне выборки стоп не мерили.
    # --stops: вместо оси R — ось sl_pct {0,50,100,200} (0.5/1/2%), sl_frac выключен.
    # --inv: те же лидеры ещё и в контр-версии <id>__inv.
    ap.add_argument("--stops", action="store_true")
    ap.add_argument("--inv", action="store_true")
    args = ap.parse_args()

    body = []
    for ld in asyncio.run(leaders()):
        for sid in [ld["strategy"]] + ([ld["strategy"] + "__inv"] if args.inv else []):
            code = f"from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('{sid}')"
            if args.stops:
                sets = [{**ld["params"], "r_inv_every": 0, "sl_frac": 0, "sl_pct": s} for s in (0, 50, 100, 200)]
            else:
                sets = [{**ld["params"], "r_inv_every": r} for r in RS]
            for q, (a, b) in QUARTERS.items():
                sym = ld["base"] + q
                body.append({
                    "campaign": f"{args.tag}{sym.lower()}{sid.replace('_', '')[:14]}",
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
