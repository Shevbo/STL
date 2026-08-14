"""Этап 2: лучшее расписание сторон (райдер 2) ПОВЕРХ райдера 1.

Порознь оба райдера уже померены: эскалация после убытка (bet_step/super_y и
статический k_avg) и расписание сторон внутри дня. Вопрос этапа 2 — складываются
ли они. Складываться они не обязаны: расписание режет число сделок, эскалация
наращивает объём после убытка, и вместе это может как усилить друг друга, так и
столкнуться (меньше сделок — реже сброс ставки после прибыли).

Расписание для каждой базы берётся ПОБЕДИВШЕЕ на этапе 1, из БД, а не руками:
переписывание чисел между этапами — ровно тот способ потерять строку, из-за
которого лидерборд уже хранит невоспроизводимые прогоны.

ВАЖНО ПРО ЧТЕНИЕ РЕЗУЛЬТАТА. Обе оси подбираются на ОДНОМ окне, и лучшая из
сотен комбинаций на одном окне это отчасти подгонка. Судить по net нельзя,
судить надо по recovery factor и по числу сделок, а вывод в реал делать только
после прогона победителя на другом контракте и другом периоде.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_rider_combo.py --dry-run
    PYTHONPATH=. $PY scripts/queue_rider_combo.py --submit
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

BETS = [(0, 0), (1, 5), (1, 10), (2, 10)]
SUPERS = [(0, 0), (1, 2), (2, 2), (2, 3)]
K_AVGS = [10, 15, 20]

TOD_KEYS = ("tod_m1", "tod_m2", "tod_s1", "tod_s2", "tod_s3")


async def best_schedules(dsn: str) -> list[tuple[str, str, str, dict, dict]]:
    """(тег, стратегия, символ, базовые параметры, победившее расписание) на базу."""
    c = await asyncpg.connect(dsn)
    try:
        rows = await c.fetch(
            "SELECT r.id, b.params, b.net_profit "
            "  FROM backtest_results b JOIN backtest_runs r ON r.id = b.run_id "
            " WHERE r.id LIKE 'camp-20260814-tod%' ORDER BY b.net_profit DESC")
    finally:
        await c.close()
    out: dict[str, tuple] = {}
    for r in rows:
        tag = r["id"].split("-")[2]
        if tag in out:
            continue                       # строки уже отсортированы по net
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else dict(p)
        if not int(p.get("tod_m1") or 0):
            continue                       # контроль без расписания нам не нужен
        parts = r["id"].split("-")
        sid, sym = parts[-2], parts[-1]
        tod = {k: int(p.get(k) or 0) for k in TOD_KEYS}
        base = {k: v for k, v in p.items()
                if k not in TOD_KEYS and k not in ("bet_step", "bet_max",
                                                   "super_y", "super_z")}
        out[tag] = (tag, sid, sym, base, tod)
    return list(out.values())


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    bases = await best_schedules(dsn)
    if not bases:
        raise SystemExit("этап 1 не найден: сначала queue_tod_rider.py")

    sets = [{"bet_step": bs, "bet_max": bm, "super_y": sy, "super_z": sz, "k_avg": k}
            for bs, bm in BETS for sy, sz in SUPERS for k in K_AVGS]

    jobs = []
    for tag, sid, sym, base, tod in bases:
        code = (f"from trader.lab.strategies.library import make_on_bar\n"
                f"on_bar = make_on_bar('{sid}')")
        jobs.append({"campaign": f"combo{tag[3:]}", "scriptCode": code, "symbol": sym,
                     "baseParams": dict(base, symbol=sym, **tod),
                     "dateFrom": DATE_FROM, "dateTo": DATE_TO, "engine": "remote",
                     "paramSets": sets})
        print(f"  {tag:10s} {sid:16s} {sym}  расписание "
              f"{tod['tod_m1']}/{tod['tod_m2']} "
              f"маска {tod['tod_s1']}{tod['tod_s2']}{tod['tod_s3']}")

    print(f"баз {len(jobs)} × комбо {len(sets)} = {len(jobs) * len(sets)} прогонов")
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
