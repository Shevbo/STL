#!/usr/bin/env python3
"""Свод перебора по инструментам с ЧЕСТНОЙ стоимостью круга.

Отчёты, которые пишет sweep_instruments.py на лету, считают порог безубытка по
одной комиссии. Этого мало: бэктест исполняет по открытию СЛЕДУЮЩЕГО бара и
спред не моделирует вовсе, а робот на агенте кроссит спред и теряет за круг
минимум один шаг цены. Для RI шаг — 16 ₽ против 24 ₽ сбора, то есть 40% истинной
стоимости; без поправки индекс выглядел вдвое привлекательнее, чем есть.

Здесь всё пересчитано из БД по прогонам кампаний:
    круг = 2 × тейкерский сбор (по МЕДИАННОЙ цене окна) + один шаг цены

Один шаг — ПОЛ, а не оценка: в тонкой книге спред шире, и тогда любой вывод
«почти окупается» становится только хуже.

Запуск: cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
        poetry run python scripts/sweep_summary.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from trader.config import Settings  # noqa: E402
from trader.lab.commission import commission_for  # noqa: E402
from trader.lab.iss_loader import fetch_contract_spec  # noqa: E402

# кампания -> (базовый код склейки, серия для сбора)
CAMPAIGNS = {
    "camp-20260802-wrgznight%": ("GZ", "GZU6"),
    "camp-20260802-wrrisweep%": ("RI", "RIU6"),
    "camp-20260802-wrgdsweep%": ("GD", "GDU6"),
    "camp-20260802-wrsisweep%": ("Si", "SiU6"),
    "camp-20260802-wrrifreq%": ("RI", "RIU6"),
}
MIN_TRADES = 30
OUT_MD = os.path.expanduser("~/sweep_summary.md")


def _params(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return {}
    return v or {}


def median_price(key: str, a: dt.date, b: dt.date) -> float:
    try:
        rows = json.load(open(f"agent_bars/{key}.json", encoding="utf-8"))["rows"]
    except Exception:  # noqa: BLE001
        return 0.0
    lo = dt.datetime(a.year, a.month, a.day, tzinfo=dt.timezone.utc).timestamp()
    hi = dt.datetime(b.year, b.month, b.day, tzinfo=dt.timezone.utc).timestamp() + 86399
    px = [r[4] for r in rows if lo <= r[0] <= hi]
    return statistics.median(px) if px else 0.0


async def main() -> int:
    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url, min_size=1, max_size=2)
    L = ["# Williams %R: свод по инструментам (издержки с учётом спреда)\n",
         "Порог безубытка = 2 × тейкерский сбор + ОДИН ШАГ ЦЕНЫ. Шаг входит потому, "
         "что бэктест исполняет по открытию следующего бара и спред не моделирует, "
         "а робот кроссит спред. Один шаг — пол, в тонкой книге хуже.\n"]
    table = ["| Инстр. | Окно | Комбинаций | Сбор, ₽ | Шаг, ₽ | Круг, ₽ | "
             "Чистый > 0 | Валовый > 0 | Валовый на круг | Доля безубытка |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    try:
        for like, (key, fee_sym) in CAMPAIGNS.items():
            runs = await pool.fetch(
                "SELECT id, date_from::date df, date_to::date dt FROM backtest_runs "
                "WHERE id LIKE $1 AND status='done' ORDER BY created_at", like)
            if not runs:
                continue
            spec = await fetch_contract_spec(fee_sym) or {}
            pv = float(spec.get("point_value") or 1.0)
            tick = float(spec.get("step_price") or 0.0)
            byw: dict = {}
            for r in runs:
                rows = await pool.fetch(
                    "SELECT params, net_profit, total_trades FROM backtest_results "
                    "WHERE run_id=$1 AND net_profit IS NOT NULL AND total_trades IS NOT NULL",
                    r["id"])
                byw.setdefault((r["df"], r["dt"]), []).extend(
                    [(_params(x["params"]), float(x["net_profit"]), int(x["total_trades"]))
                     for x in rows])
            for (a, b), rows in sorted(byw.items()):
                rows = [x for x in rows if x[2] >= MIN_TRADES]
                if len(rows) < 50:          # проба конвейера, не окно перебора
                    continue
                px = median_price(key, a, b)
                fee = 2 * commission_for(fee_sym, px, 1, pv, True) if px else 0.0
                cost = fee + tick
                gross = [(net + tr * cost, net, tr) for _, net, tr in rows]
                per = [g / t for g, _, t in gross if t]
                share = statistics.median(per) / cost if cost else 0
                table.append(
                    f"| {key} | {a:%d.%m.%y}—{b:%d.%m.%y} | {len(rows)} | {fee:.2f} | "
                    f"{tick:.2f} | {cost:.2f} | {sum(1 for _, n, _ in gross if n > 0)} | "
                    f"{sum(1 for g, _, _ in gross if g > 0)} | "
                    f"{statistics.median(per):+.2f} ₽ | **{100 * share:.0f}%** |")
        L += table
        L.append("\n**Как читать.** «Валовый > 0» — сколько комбинаций прибыльны ДО "
                 "издержек: это про предсказательную силу сигнала. «Чистый > 0» — "
                 "сколько прибыльны после. «Доля безубытка» — какую часть истинной "
                 "стоимости круга покрывает медианный валовый эдж; 100% и выше = "
                 "стратегия окупает оборот.\n")
        open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
        print("\n".join(L))
        print(f"\n(сохранено: {OUT_MD})")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
