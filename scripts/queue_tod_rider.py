"""Перебор расписания сторон внутри дня (райдер 2) на нескольких стратегиях.

Гипотеза оператора 14.08.2026: до 11:11 только лонг, до 19:00 только шорт,
вечером обе. Проверяем не её одну, а ВСЁ пространство масок — иначе, попав в
плюс, мы не отличим найденную закономерность от везения на одной комбинации.

Этап 1 (этот файл): расписание САМО ПО СЕБЕ. 4 маски × 3 окна = 64 сочетания на
каждую пару границ, две пары границ, четыре базы. Маска 0 (вне рынка) включена
намеренно: «не торговать утром» — такая же гипотеза, как «торговать утром лонг».

Этап 2 запускается отдельно, лучшим расписанием каждой базы поверх райдера 1
(bet_step/super_y/k_avg) — сначала надо знать, какое расписание вообще лучшее.

Базы разные по СЕМЕЙСТВУ, а не только по инструменту: расписание, которое
помогает трендовой стратегии и не помогает пробойной, это находка; расписание,
которое помогает одной строке, это подгонка.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_tod_rider.py --dry-run
    PYTHONPATH=. $PY scripts/queue_tod_rider.py --submit
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
DATE_FROM, DATE_TO = "2026-05-04", "2026-08-08"

# Границы окон в минутах от полуночи МСК. Первая пара — гипотеза оператора
# (11:11 и 19:00), вторая — круглая альтернатива: если результат держится только
# на одной паре с точностью до минуты, это подгонка под час, а не режим дня.
BOUNDS = [(671, 1140), (600, 1080)]
MASKS = [0, 1, 2, 3]        # 0 вне рынка, 1 лонг, 2 шорт, 3 обе

# Живой реальный робот: его расписание интересует нас в первую очередь.
LIVE_MACD = {"qty": 1, "avg_max": 20, "fast": 57, "slow": 48, "signal": 10,
             "tp_atr": 40, "avg_atr_n": 25, "avg_step_atr": 21, "min_gap_pts": 0,
             "cooldown_min": 0, "cooldown_pct": 1, "nd_days": 5, "gap_auto": 0,
             "k_avg": 10, "sl_frac": 0, "sl_pct": 100, "allow_long": 1,
             "allow_short": 1, "dv_bars": 60, "dv_range_pts": 300}
BOLLBO = {"qty": 1, "period": 23, "mult": 30, "tp_atr": 60, "avg_max": 7,
          "avg_step_atr": 10, "avg_atr_n": 14, "min_gap_atr": 13, "min_gap_pts": 0,
          "sl_pct": 67, "sl_frac": 0, "cooldown_min": 0, "cooldown_pct": 1,
          "allow_long": 1, "allow_short": 1, "dv_bars": 0, "dv_range_pts": 0}


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


async def top_row(dsn: str, strategy: str, symbol: str) -> dict | None:
    """Лучшая по net строка стратегии из лидерборда — как четвёртая база.

    Берём из БД, а не хардкодим: своя строка у trend-семейства нужна честная, а
    не выдуманная, иначе расписание будет проверено на конфиге, который и без
    него не работает.
    """
    c = await asyncpg.connect(dsn)
    try:
        r = await c.fetchrow(
            "SELECT params FROM optimization_leaderboard "
            " WHERE strategy=$1 AND symbol=$2 AND total_trades > 150 "
            " ORDER BY net_profit DESC NULLS LAST LIMIT 1", strategy, symbol)
    finally:
        await c.close()
    if r is None:
        return None
    p = r["params"]
    return json.loads(p) if isinstance(p, str) else dict(p)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    tsma = await top_row(dsn, "triple_sma", "RIU6")
    bases = [("todmacd", "macd_shectory1", "RIU6", dict(LIVE_MACD)),
             ("todbollu", "bollinger_bo", "BRU6", dict(BOLLBO)),
             ("todbollq", "bollinger_bo", "BRQ6", dict(BOLLBO))]
    if tsma:
        tsma.pop("symbol", None)
        bases.append(("todtsma", "triple_sma", "RIU6", tsma))
    else:
        print("triple_sma RIU6 в лидерборде не найден — идём тремя базами")

    # Контроль (расписание выключено) кладём в КАЖДОЕ задание: сравнивать надо с
    # прогоном той же сборки на тех же данных, а не с числом из лидерборда.
    sets = [{"tod_m1": 0, "tod_m2": 0}]
    sets += [{"tod_m1": m1, "tod_m2": m2, "tod_s1": s1, "tod_s2": s2, "tod_s3": s3}
             for m1, m2 in BOUNDS for s1 in MASKS for s2 in MASKS for s3 in MASKS
             if not (s1 == 0 and s2 == 0 and s3 == 0)]

    jobs = [{"campaign": tag, "scriptCode": script_code(sid), "symbol": sym,
             "baseParams": dict(p, symbol=sym), "dateFrom": DATE_FROM,
             "dateTo": DATE_TO, "engine": "remote", "paramSets": sets}
            for tag, sid, sym, p in bases]

    print(f"баз {len(jobs)} × комбо {len(sets)} = {len(jobs) * len(sets)} прогонов")
    for tag, sid, sym, _ in bases:
        print(f"  {tag:10s} {sid:16s} {sym}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        ok = 0
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code not in (200, 201, 202):
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok} из {len(jobs)}")


if __name__ == "__main__":
    asyncio.run(main())
