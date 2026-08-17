"""Этап 2 ночной охоты: выжившие с двух окон — на все остальные режимы.

Этап 1 (queue_night_hunt.py) отсеивает по дешёвому признаку: плюс И на растущем
окне RI, И на падающем. Здесь выжившие предъявляются тому, чего не видели:

  ДРУГОЙ РОСТ RI    RIZ5 ноя-дек, +10.2%
  РОСТ ЧУЖОГО АКТИВА SiU6 май-авг +8.2%, GZH6 ноя-янв +4.2% — если преимущество
                    от стратегии, а не от подгонки под ноябрьский RI, оно обязано
                    проявиться и там
  ДРУГОЕ ПАДЕНИЕ    RIM6 фев-июнь −9.7%, GZU6 май-авг −26.5%
  БОКОВИК           SRH6 ноя-янв +0.2% — третий режим, без него «универсальный
                    солдат» остаётся непроверенным словом
  ВПЕРЁД            RIU6 08-08..08-17: баров при отборе не существовало

Пунктовые пороги (dv_range_pts, min_gap_pts, signals2ignor_value) пересчитываются
по цене инструмента: 300 пунктов на RI при 78 000 это 0.4%, а на газе при 9 000 —
3.3%, то есть другой фильтр. На золоте этот перенос уже съел целый прогон.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_night_stage2.py --dry-run
    PYTHONPATH=. $PY scripts/queue_night_stage2.py --submit
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

# (тег, символ, начало, конец, множитель пунктов к цене RI, что было с рынком)
RUNS = [
    ("s2risez", "RIZ5", "2025-11-17", "2025-12-17", 1.0, "RI рос +10.2%"),
    ("s2risesi", "SiU6", "2026-05-04", "2026-08-07", 1.09, "Si рос +8.2%"),
    ("s2risegz", "GZH6", "2025-11-17", "2026-01-24", 0.115, "GZ рос +4.2%"),
    ("s2fallm", "RIM6", "2026-02-01", "2026-06-17", 1.0, "RI падал −9.7%"),
    ("s2fallgz", "GZU6", "2026-05-04", "2026-08-07", 0.115, "GZ падал −26.5%"),
    ("s2flat", "SRH6", "2025-11-17", "2026-01-24", 0.36, "SR стоял +0.2%"),
    ("s2fwd", "RIU6", "2026-08-08", "2026-08-17", 1.0, "вперёд, −10.6%"),
]
PTS_KEYS = ("dv_range_pts", "min_gap_pts", "signals2ignor_value")


def rescale(cfg: dict, k: float) -> dict:
    if k == 1.0:
        return dict(cfg)
    out = dict(cfg)
    for key in PTS_KEYS:
        if out.get(key):
            out[key] = max(1, round(float(out[key]) * k))
    return out


async def survivors(dsn: str, min_trades: int, limit_per_strategy: int) -> dict[str, list[dict]]:
    """Конфиги, плюсовые на ОБОИХ окнах этапа 1, по стратегиям."""
    c = await asyncpg.connect(dsn)
    try:
        rows = await c.fetch(
            """select r.strategy, r.id, b.params, b.net_profit, b.total_trades
                 from backtest_results b join backtest_runs r on r.id = b.run_id
                where (r.id like 'camp-%-nhrise%' or r.id like 'camp-%-nhfall%')
                  and r.created_at > now() - interval '1 day'""")
    finally:
        await c.close()
    by: dict = {}
    for r in rows:
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else dict(p)
        key = (r["strategy"], json.dumps({k: v for k, v in sorted(p.items())
                                          if k != "symbol"}, sort_keys=True))
        side = "rise" if "-nhrise" in r["id"] else "fall"
        cur = by.setdefault(key, {})
        cur[side] = (r["net_profit"] or 0, r["total_trades"] or 0)
    out: dict[str, list[dict]] = {}
    for (strategy, pjson), v in by.items():
        if len(v) < 2:
            continue
        if not all(net > 0 for net, _ in v.values()):
            continue
        if min(tr for _, tr in v.values()) < min_trades:
            continue          # горстка сделок — не результат, а совпадение
        rec = {"params": json.loads(pjson),
               "score": min(net for net, _ in v.values()),   # слабое звено
               "rise": v["rise"][0], "fall": v["fall"][0]}
        out.setdefault(strategy, []).append(rec)
    for s in out:
        out[s].sort(key=lambda x: -x["score"])
        del out[s][limit_per_strategy:]
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--min-trades", type=int, default=40)
    ap.add_argument("--per-strategy", type=int, default=25)
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    surv = await survivors(dsn, args.min_trades, args.per_strategy)
    if not surv:
        raise SystemExit("этап 1 не дал ни одного выжившего — этап 2 запускать не на чем")

    jobs, total = [], 0
    for strategy, recs in sorted(surv.items()):
        code = ("from trader.lab.strategies.library import make_on_bar\n"
                f"on_bar = make_on_bar('{strategy}')")
        print(f"  {strategy:18s} выживших {len(recs):3d} | лучшее слабое звено "
              f"{recs[0]['score']:>10,.0f}")
        for tag, sym, a, b, k, _note in RUNS:
            sets = [rescale(r["params"], k) for r in recs]
            jobs.append({"campaign": f"{tag}{strategy[:10].replace('_', '')}",
                         "scriptCode": code, "symbol": sym,
                         "baseParams": dict(sets[0], symbol=sym),
                         "dateFrom": a, "dateTo": b, "engine": "remote",
                         "paramSets": sets})
            total += len(sets)
    print(f"\nстратегий {len(surv)} | заданий {len(jobs)} | прогонов {total:,}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=120) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            ok += r.status_code in (200, 201, 202)
            if r.status_code not in (200, 201, 202):
                print(f"  ошибка {r.status_code} на {j['campaign']}: {r.text[:160]}")
    print(f"поставлено {ok} из {len(jobs)}")


if __name__ == "__main__":
    asyncio.run(main())
