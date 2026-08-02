#!/usr/bin/env python3
"""У каких роботов честная (тейкерская) комиссия переворачивает вывод.

Карточка робота считала комиссию по МЕЙКЕРСКОЙ модели — только брокерские 0.45 ₽
с филла, без биржевого сбора. Робот, отправленный на агент, кроссит спред и платит
сбор от объёма, поэтому все экраны роботов переведены на тейкера (02.08.2026).
Цифры на витрине после этого поедут вниз — этот отчёт показывает, у кого именно и
насколько, чтобы утреннее «всё подешевело» не выглядело поломкой.

Разница = Σ по филлам (ставка_группы × цена × ₽/пункт × контрактов) — ровно
биржевая часть, которой раньше не было. Считает по live_trades, без i9.

Запуск: cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
        poetry run python scripts/fee_model_impact.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings  # noqa: E402
from trader.lab.commission import commission_for  # noqa: E402
from trader.lab.iss_loader import fetch_contract_spec  # noqa: E402

EXECUTED = ("filled", "executed", "done", "partial")


async def main() -> int:
    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            """SELECT lt.robot_id, r.name, lt.symbol, lt.price, lt.quantity qty
                 FROM live_trades lt LEFT JOIN robots r ON r.id = lt.robot_id
                WHERE lower(lt.status) = ANY($1::text[])
                  AND lt.price IS NOT NULL AND lt.quantity IS NOT NULL""",
            list(EXECUTED))
        if not rows:
            print("исполненных филлов не найдено")
            return 0
        pv: dict[str, float] = {}
        for sym in sorted({r["symbol"] for r in rows if r["symbol"]}):
            spec = await fetch_contract_spec(sym)
            pv[sym] = (spec or {}).get("point_value") or 1.0
            await asyncio.sleep(0.15)

        agg: dict = {}
        for r in rows:
            sym = r["symbol"] or ""
            v = pv.get(sym, 1.0)
            q = abs(int(r["qty"]))
            price = float(r["price"])
            taker = commission_for(sym, price, q, v, True)
            maker = commission_for(sym, price, q, v, False)
            a = agg.setdefault(r["robot_id"], {"name": r["name"] or r["robot_id"],
                                               "fills": 0, "maker": 0.0, "taker": 0.0,
                                               "syms": set()})
            a["fills"] += 1
            a["maker"] += maker
            a["taker"] += taker
            a["syms"].add(sym)

        out = sorted(agg.values(), key=lambda a: a["taker"] - a["maker"], reverse=True)
        print(f"{'робот':30} {'филлов':>7} {'мейкер, ₽':>11} {'тейкер, ₽':>11} "
              f"{'разница, ₽':>11}  инструменты")
        print("-" * 96)
        tm = tt = 0.0
        for a in out:
            if a["fills"] < 10:
                continue
            tm += a["maker"]
            tt += a["taker"]
            print(f"{a['name'][:30]:30} {a['fills']:7d} {a['maker']:11,.0f} "
                  f"{a['taker']:11,.0f} {a['taker'] - a['maker']:11,.0f}  "
                  f"{','.join(sorted(a['syms']))[:24]}".replace(",", " ").replace(" ", " ", 1))
        print("-" * 96)
        print(f"{'ИТОГО':30} {'':>7} {tm:11,.0f} {tt:11,.0f} {tt - tm:11,.0f}"
              .replace(",", " "))
        print("\nРазница — это биржевой сбор, которого карточка раньше не показывала.")
        print("У робота, чей результат меньше своей разницы, честная модель меняет знак.")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
