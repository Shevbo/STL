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

def _params(v):
    if isinstance(v, str):
        import json
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return {}
    return v or {}


SWEPT = ("period", "oversold", "overbought", "tp_atr", "sl_pct")


def _key(p: dict) -> tuple:
    """Ключ комбинации по ПЕРЕБИРАЕМЫМ осям — сравнивать окна можно только по ним."""
    return tuple((k, p.get(k)) for k in SWEPT)


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
            # params приходит СТРОКОЙ: json-кодек регистрируется на пуле приложения
            # (trader.db._setup_json_codec), а этот пул свой.
            by_window.setdefault((r["df"], r["dt"]), []).extend(
                [(_params(x["params"]), float(x["net_profit"]), int(x["total_trades"]))
                 for x in rows])

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

        # Пересечение окон по ВАЛОВОМУ. Одно окно с положительным валовым — это
        # выборка; повторяемость на всех окнах — уже кандидат в преимущество.
        # Если пересечение пусто, спорить не о чем: сигнал не работает ни в один
        # режим рынка, и вопрос комиссии даже не встаёт.
        # Крошечные окна (проба конвейера на десяток комбинаций) в пересечение не
        # берём: раньше такое окно не выпадало, а ОБНУЛЯЛО пересечение целиком.
        keyed = []
        for rows in by_window.values():
            w = {_key(p): (net + tr * fee_round, net, tr)
                 for p, net, tr in rows if tr >= 30}
            if len(w) > 100:
                keyed.append(w)
        full = [k for k in keyed[0] if all(k in w for w in keyed)] if len(keyed) > 1 else []
        both_gross = [k for k in full if all(w[k][0] > 0 for w in keyed)]
        both_net = [k for k in full if all(w[k][1] > 0 for w in keyed)]
        print(f"── пересечение {len(keyed)} окон ({len(full)} общих комбинаций)")
        print(f"   валовый > 0 во ВСЕХ окнах: {len(both_gross)}")
        print(f"   чистый  > 0 во ВСЕХ окнах: {len(both_net)}")
        for k in both_gross[:5]:
            print(f"     {dict(k)}")
            for i, w in enumerate(keyed):
                g, n, t = w[k]
                print(f"       окно {i + 1}: валовый {g:+,.0f} ₽, чистый {n:+,.0f} ₽, "
                      f"сделок {t}".replace(",", " "))
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
