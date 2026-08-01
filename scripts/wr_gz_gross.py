#!/usr/bin/env python3
"""Разложить результат перебора на ВАЛОВЫЙ и КОМИССИЮ — по прогонам ночной кампании.

Зачем. Убыток сам по себе ничего не объясняет: непонятно, стратегия ошибается или
просто платит за оборот. Разложение отвечает сразу:
  • валовый ≈ 0  -> преимущества нет НИ В ОДНУ сторону, контр-версия (__inv) тоже
    не спасёт: она заплатит ту же комиссию за тот же ноль;
  • валовый заметно < 0 -> сигнал систематически неверен, есть смысл смотреть __inv;
  • валовый > комиссии -> преимущество есть, вопрос только в издержках.

Комиссия оценивается как сделки × 2 филла × тейкерский сбор по СРЕДНЕЙ цене окна
(биржевой сбор считается от объёма, поэтому нужна цена, а не только число сделок).
Это оценка ±пара процентов, и она подписана как оценка.

Запуск на хостере:
  cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
  poetry run python scripts/wr_gz_gross.py
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings  # noqa: E402
from trader.lab.commission import commission_for  # noqa: E402

CAMPAIGN_LIKE = os.environ.get("WRGZ_CAMPAIGN", "camp-20260802-wrgznight%")
SYMBOL = "GZU6"          # для сбора важна ГРУППА инструмента, а не конкретная серия
AVG_PRICE = 9600.0       # средняя цена газа за период, пунктов
POINT_VALUE = 1.0


async def main() -> int:
    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url, min_size=1, max_size=2)
    try:
        runs = await pool.fetch(
            "SELECT id, date_from::date df, date_to::date dt FROM backtest_runs "
            "WHERE id LIKE $1 AND status='done' ORDER BY created_at", CAMPAIGN_LIKE)
        fee_round = 2 * commission_for(SYMBOL, AVG_PRICE, 1, POINT_VALUE, True)
        print(f"тейкерская комиссия круга при цене {AVG_PRICE:.0f}: {fee_round:.2f} ₽\n")
        by_window: dict = {}
        for r in runs:
            rows = await pool.fetch(
                "SELECT params, net_profit, total_trades FROM backtest_results "
                "WHERE run_id=$1 AND total_trades IS NOT NULL AND net_profit IS NOT NULL",
                r["id"])
            by_window.setdefault((r["df"], r["dt"]), []).extend(
                [(x["params"], float(x["net_profit"]), int(x["total_trades"])) for x in rows])

        for (df, dt), rows in sorted(by_window.items()):
            if not rows:
                continue
            gross = [(net + tr * fee_round, net, tr, p) for p, net, tr in rows]
            nets = [g[1] for g in gross]
            grs = [g[0] for g in gross]
            trs = [g[2] for g in gross]
            win = sum(1 for n in nets if n > 0)
            gwin = sum(1 for g in grs if g > 0)
            best = max(gross, key=lambda g: g[1])
            # Цена одной сделки: сколько валового приносит средняя сделка. Порог —
            # комиссия круга; всё, что ниже, оборотом не отбить никогда.
            per_trade = [g[0] / g[2] for g in gross if g[2] >= 30]
            print(f"── окно {df} … {dt}: комбинаций {len(rows)}")
            print(f"   сделок: медиана {int(statistics.median(trs))}, "
                  f"минимум {min(trs)}, максимум {max(trs)}")
            print(f"   ЧИСТЫЙ: в плюсе {win} из {len(rows)}, "
                  f"лучший {best[1]:+,.0f} ₽, медиана {statistics.median(nets):+,.0f} ₽"
                  .replace(",", " "))
            print(f"   ВАЛОВЫЙ (до комиссии): в плюсе {gwin} из {len(rows)}, "
                  f"лучший {max(grs):+,.0f} ₽, медиана {statistics.median(grs):+,.0f} ₽"
                  .replace(",", " "))
            if per_trade:
                print(f"   валовый НА СДЕЛКУ: медиана {statistics.median(per_trade):+.2f} ₽ "
                      f"при пороге безубытка {fee_round:.2f} ₽")
            bp = {k: v for k, v in (best[3] or {}).items() if k != "symbol"}
            print(f"   лучший набор: {bp}")
            print(f"   у него: чистый {best[1]:+,.0f} ₽, валовый {best[0]:+,.0f} ₽, "
                  f"сделок {best[2]}".replace(",", " "))
            print()
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
