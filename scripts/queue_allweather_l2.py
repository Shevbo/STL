"""Уровень 2 для «всепогодной» девятки: улики, которых НЕ БЫЛО в отборе.

Девять конфигов отобраны как плюсовые на четырёх окнах RI сразу — двух растущих
(RIH6, RIZ5) и двух падающих (RIU6, RIM6). Это отбор лучших 9 из 723 на тех же
данных, поэтому сам по себе он ничего не доказывает: подгонка выглядит ровно так
же. Уровень 2 предъявляет то, чего конфиг не видел:

  ВПЕРЁД        RIU6 после конца окна отбора. Короткое окно, зато честное: этих
                баров при отборе не существовало вовсе.
  ДРУГОЙ АКТИВ  тот же конфиг на Si, GZ, SR. Расписание сторон — утверждение о
                ВРЕМЕНИ СУТОК, а не об инструменте; если эффект настоящий, он
                обязан хоть как-то проявиться на соседях. Если он живёт только
                на RI — это подгонка под RI, как бы красиво ни выглядели окна.

Окна не независимы (RIZ5 лежит внутри RIH6, RIU6 и RIM6 пересекаются), так что
исходные «четыре наблюдения» — это два режима. Именно поэтому вес имеет чужой
инструмент, а не пятое окно того же RI.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_allweather_l2.py --dry-run
    PYTHONPATH=. $PY scripts/queue_allweather_l2.py --submit
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

KEEP = ("fast", "slow", "signal", "tp_atr", "avg_max", "avg_step_atr", "avg_atr_n",
        "k_avg", "min_gap_pts", "min_gap_atr", "cooldown_min", "cooldown_pct",
        "nd_days", "gap_auto", "sl_frac", "sl_pct", "dv_bars", "dv_range_pts",
        "allow_long", "allow_short", "tod_m1", "tod_m2", "tod_s1", "tod_s2",
        "tod_s3", "bet_step", "bet_max", "super_y", "super_z", "qty")
NEED = ("RIH6", "RIZ5", "RIU6", "RIM6")

# (тег, символ, начало, конец). Заполняется по факту наличия баров — пустое окно
# даёт ноль сделок и читается как «конфиг не работает», хотя работать было не на чем.
RUNS = [
    # (тег, символ, начало, конец, множитель цены к RI, что было с рынком)
    ("awfwd", "RIU6", "2026-08-08", "2026-08-17", 1.0, "вперёд, −10.6%"),
    ("awsi", "SiU6", "2026-05-04", "2026-08-07", 1.09, "Si РОС +8.2%"),
    ("awsih", "SiH6", "2025-11-17", "2026-01-24", 1.09, "Si падал −9.2%"),
    ("awgz", "GZU6", "2026-05-04", "2026-08-07", 0.115, "GZ падал −26.5%"),
    ("awgzh", "GZH6", "2025-11-17", "2026-01-24", 0.115, "GZ рос +4.2%"),
    ("awsr", "SRU6", "2026-05-04", "2026-08-07", 0.36, "SR падал −5.7%"),
    ("awsrh", "SRH6", "2025-11-17", "2026-01-24", 0.36, "SR стоял +0.2%"),
]


def rescale(cfg: dict, k: float) -> dict:
    """Пересчитать параметры, заданные В ПУНКТАХ, под цену другого инструмента.

    «Долина смерти» меряет коридор в пунктах: 300 пт при цене RI 78 000 — это
    0.38%, а при цене GZ 9 000 — уже 3.3%, то есть боковик ВСЕГДА и ноль сделок.
    Ровно на этом 14.08 сорвался прогон по золоту: шесть конфигов, ноль сделок,
    и это читалось как «стратегия не работает». Всё остальное в конфигах уже
    масштабонезависимо — доли ATR, а не пункты.
    """
    if k == 1.0:
        return dict(cfg)
    out = dict(cfg)
    for key in ("dv_range_pts", "min_gap_pts"):
        v = float(out.get(key) or 0)
        if v:
            out[key] = max(1, round(v * k))
    return out


async def winners(dsn: str) -> list[dict]:
    c = await asyncpg.connect(dsn)
    try:
        rows = await c.fetch(
            """select r.symbol, b.params, b.net_profit
                 from backtest_results b join backtest_runs r on r.id = b.run_id
                where (r.id like 'camp-%-rise%' or r.id like 'camp-%-fall%'
                       or r.id like 'camp-%-refine%')
                  and r.created_at > now() - interval '4 day'""")
    finally:
        await c.close()
    by: dict = {}
    for r in rows:
        p = r["params"]
        p = json.loads(p) if isinstance(p, str) else dict(p)
        key = tuple((x, p.get(x)) for x in KEEP)
        cur = by.setdefault(key, {})
        prev = cur.get(r["symbol"])
        if prev is None or (r["net_profit"] or 0) > prev:
            cur[r["symbol"]] = r["net_profit"] or 0
    out = [dict(k) for k, v in by.items()
           if set(NEED) <= set(v) and all(v[s] > 0 for s in NEED)]
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--only", default="", help="ограничить теги, через запятую")
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    cfgs = await winners(dsn)
    if not cfgs:
        raise SystemExit("всепогодных конфигов не найдено — проверь кампании rise/fall")

    code = ("from trader.lab.strategies.library import make_on_bar\n"
            "on_bar = make_on_bar('macd_shectory1')")
    only = {t for t in args.only.split(",") if t}
    jobs = []
    for tag, sym, a, b, k, note in RUNS:
        if only and tag not in only:
            continue
        sets = [rescale(c, k) for c in cfgs]
        # Один прогон на символ+окно, девять конфигов как paramSets: так они
        # считаются на ОДНИХ И ТЕХ ЖЕ барах, и разница между ними — их разница,
        # а не разница загрузок.
        jobs.append({"campaign": tag, "scriptCode": code, "symbol": sym,
                     "baseParams": dict(sets[0], symbol=sym),
                     "dateFrom": a, "dateTo": b, "engine": "remote", "priority": 100,
                     "paramSets": sets})

    print(f"конфигов {len(cfgs)} × прогонов {len(jobs)} = {len(cfgs) * len(jobs)} комбо")
    for tag, sym, a, b, k, note in RUNS:
        if not only or tag in only:
            scaled = "" if k == 1.0 else f" (пункты ×{k})"
            print(f"  {tag:7s} {sym:5s} {a}..{b}  {note}{scaled}")
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
