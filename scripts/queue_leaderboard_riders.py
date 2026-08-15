"""Топ-5 строк КАЖДОЙ стратегии из хитпарада, прогнанные с райдерами.

Заказ оператора 14.08.2026: взять то, что хитпарад уже показывает прибыльным, и
проверить, что с этим делают райдеры — эскалация после убытка и расписание сторон
внутри дня.

ОТБОР БАЗ. Хитпарад хранит миллионы строк за годы, и брать оттуда «топ по net»
нельзя: половина верхних строк это вырожденные пары периодов (осциллятора нет, а
знак есть), прогревы длиннее, чем раннер вообще помнит баров, и инструменты,
которых нет в белом списке агента. Поэтому базы фильтруются теми же проверками,
что и гейт кандидата уровня 1 — они импортируются ОТТУДА, а не переписываются:
разошедшись, две копии проверки дадут два разных ответа на один вопрос.

qty у каждой базы прижимается к единице. В хитпараде попадаются строки с qty=19,
и их net в 19 раз больше не от стратегии — сравнивать такие с однолотовыми
нельзя (14.08 на этом чуть не потеряли полдня).

СЕТКА РАЙДЕРОВ сознательно короткая: это не поиск оптимума, а проверка, живёт ли
эффект на чужих конфигах. Оптимум ищется потом и только для выживших.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_leaderboard_riders.py --dry-run
    PYTHONPATH=. $PY scripts/queue_leaderboard_riders.py --submit --top 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from candidate_gate import RUNNER_BAR_TAIL, TRADABLE, degenerate, warmup_bars  # noqa: E402

from trader.auth.portal import make_session_token  # noqa: E402

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
DATE_FROM, DATE_TO = "2026-05-04", "2026-08-08"

# (bet_step, bet_max, super_y, super_z). Победители 14.08: работает super, а
# ставочная система почти не даёт вклада — но проверяем обе и обе разом.
RIDER1 = [(0, 0, 0, 0), (2, 10, 0, 0), (0, 0, 2, 3), (2, 10, 2, 3)]
K_AVGS = [10, 20]
# (m1, m2, s1, s2, s3). Первая строка — расписание выключено. Дальше победители
# 14.08: круглые границы 10:00/18:00 оказались лучше 11:11/19:00 на всех базах,
# а лучшая маска у трёх баз из четырёх начиналась с ШОРТА утром.
RIDER2 = [(0, 0, 3, 3, 3),
          (600, 1080, 3, 2, 1),
          (600, 1080, 1, 2, 3),
          (600, 1080, 2, 3, 2),
          (671, 1140, 2, 3, 3)]


def script_code(sid: str) -> str:
    base = sid[:-len("__inv")] if sid.endswith("__inv") else sid
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')" if base == sid \
        else f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


async def bases(dsn: str, top: int) -> list[tuple[str, str, dict]]:
    """Топ-N живых строк каждой стратегии: (стратегия, символ, параметры)."""
    c = await asyncpg.connect(dsn)
    try:
        rows = await c.fetch(
            "SELECT strategy, symbol, params, net_profit, total_trades, max_mae "
            "  FROM optimization_leaderboard "
            " WHERE net_profit > 0 AND total_trades >= 150 AND max_mae > 0 "
            " ORDER BY net_profit DESC LIMIT 20000")
    finally:
        await c.close()
    picked: dict[str, list] = {}
    seen: set[tuple] = set()
    for r in rows:
        sid, sym = r["strategy"], r["symbol"]
        if sym not in TRADABLE:
            continue
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else dict(p)
        if degenerate(p):                       # вырожденная пара периодов
            continue
        w = warmup_bars(sid, p)
        if w is not None and w > RUNNER_BAR_TAIL:
            continue                            # прогрев не влезает в память раннера
        key = (sid, sym, tuple(sorted((k, str(v)) for k, v in p.items()
                                      if k not in ("qty", "symbol"))))
        if key in seen:
            continue
        seen.add(key)
        lst = picked.setdefault(sid, [])
        if len(lst) < top:
            lst.append((sid, sym, p))
    return [x for lst in picked.values() for x in lst]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5, help="строк на стратегию")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    bs = await bases(dsn, args.top)
    if not bs:
        raise SystemExit("в хитпараде не нашлось строк, переживших фильтры")

    sets = [{"bet_step": b1, "bet_max": b2, "super_y": sy, "super_z": sz, "k_avg": k,
             "tod_m1": m1, "tod_m2": m2, "tod_s1": s1, "tod_s2": s2, "tod_s3": s3}
            for b1, b2, sy, sz in RIDER1 for k in K_AVGS
            for m1, m2, s1, s2, s3 in RIDER2]

    by_strategy: dict[str, int] = {}
    jobs = []
    for i, (sid, sym, p) in enumerate(bs):
        n = by_strategy.get(sid, 0) + 1
        by_strategy[sid] = n
        base = {k: v for k, v in p.items()
                if k not in ("bet_step", "bet_max", "super_y", "super_z",
                             "tod_m1", "tod_m2", "tod_s1", "tod_s2", "tod_s3")}
        base["qty"] = 1                      # qty только масштабирует P&L
        jobs.append({"campaign": f"lbr{sid[:9]}{n}", "scriptCode": script_code(sid),
                     "symbol": sym, "baseParams": dict(base, symbol=sym),
                     "dateFrom": DATE_FROM, "dateTo": DATE_TO, "engine": "remote",
                     "paramSets": sets})

    print(f"баз {len(jobs)} из {len(by_strategy)} стратегий × комбо {len(sets)} "
          f"= {len(jobs) * len(sets)} прогонов")
    for sid, n in sorted(by_strategy.items()):
        print(f"  {sid:18s} {n}")
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
                print(f"  ошибка {r.status_code}: {r.text[:160]}")
    print(f"поставлено {ok} из {len(jobs)}")


if __name__ == "__main__":
    asyncio.run(main())
