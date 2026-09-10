#!/usr/bin/env python3
"""Перепроверка лидеров, посчитанных на ОБРЕЗАННОМ окне.

ЗАЧЕМ. До 10.09.2026 i9 брал кэш `agent_bars/<базовый код>.json` как первый
источник баров, проверяя только НАЧАЛО запрошенного окна. Кэш базовых кодов
заморожен со 02.08.2026 и кончается 31.07.2026 (BR/CC/CR/ED/Eu — 23.06,
GD — 02.08), поэтому каждое задание с окном правее этого дня молча считалось по
укороченной истории. За 45 дней так прошло 182 задания на базовых кодах —
266 690 строк результатов, из них 132 кампании с ПОЛОЖИТЕЛЬНЫМ верхом.

Страж починен (`opt_agent._bars_for` проверяет оба края), кэш дочитан до 10.09
(`scripts/warm_sweep_bars.py`). Но уже посчитанные числа остались: они отвечают
не на тот вопрос, который задавали. Здесь верх каждой такой кампании гоняется
ЗАНОВО на том же окне — теперь по полным барам, — и сравнивается с записанным.

ЧТО ЧИТАТЬ В РЕЗУЛЬТАТЕ. Совпало — число было честным (окно целиком лежало
внутри кэша и обрезки не было). Просело — верх держался на укороченном ряде.

    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_recheck_truncated.py --dry-run
    PYTHONPATH=. $PY scripts/queue_recheck_truncated.py --submit
    PYTHONPATH=. $PY scripts/queue_recheck_truncated.py --report
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from trader.auth.portal import make_session_token  # noqa: E402
from trader.config import Settings  # noqa: E402
from trader.lab.strategies.library import REGISTRY  # noqa: E402

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
SEEDS = "/tmp/rck_seeds.json"

# Конец кэша на момент поломки (mtime файла в agent_bars/). Окно правее этой даты
# считалось обрезанным ровно по ней.
CUT_END = {
    "RI": dt.date(2026, 7, 31), "Si": dt.date(2026, 7, 31), "GZ": dt.date(2026, 7, 31),
    "BR": dt.date(2026, 6, 23), "CC": dt.date(2026, 6, 23), "CR": dt.date(2026, 6, 23),
    "ED": dt.date(2026, 6, 23), "Eu": dt.date(2026, 6, 23), "GD": dt.date(2026, 8, 2),
}
PRIORITY = 40


def script_code(strategy: str) -> str | None:
    """Код запуска по имени стратегии из колонки strategy.

    Реестровые идут через make_on_bar (включая суффикс __inv), отдельные модули
    (rich_fool, us_open_fvg, ...) — прямым импортом. Не знаем такое имя — молчим,
    а не подставляем «что-нибудь похожее»: прогон не по той стратегии хуже
    отсутствия прогона.
    """
    base = strategy[:-len("__inv")] if strategy.endswith("__inv") else strategy
    if base in REGISTRY:
        return ("from trader.lab.strategies.library import make_on_bar\n"
                f"on_bar = make_on_bar('{strategy}')")
    path = os.path.join("trader", "lab", "strategies", f"{base}.py")
    if os.path.exists(path):
        return f"from trader.lab.strategies.{base} import on_bar, on_start, on_stop"
    return None


def canon(params) -> str:
    if isinstance(params, str):
        params = json.loads(params)
    p = {k: v for k, v in (params or {}).items() if k != "symbol"}
    return json.dumps(p, sort_keys=True, ensure_ascii=False)


async def collect(limit_per: int, min_trades: int, days: int) -> dict:
    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url, min_size=1, max_size=2)
    try:
        runs = await pool.fetch(
            "SELECT id, date_from::date df, date_to::date dt, strategy, symbol FROM"
            " backtest_runs WHERE created_at > now() - make_interval(days => $1)"
            " AND status='done' AND symbol = any($2::text[])",
            days, list(CUT_END))
        picked: dict = {}
        for r in runs:
            cut = CUT_END.get(r["symbol"])
            if not cut or r["dt"] <= cut:
                continue
            rows = await pool.fetch(
                "SELECT params, net_profit, total_trades FROM backtest_results"
                " WHERE run_id=$1 AND net_profit > 0 AND total_trades >= $2"
                " ORDER BY net_profit DESC LIMIT 40", r["id"], min_trades)
            key = (r["symbol"], r["df"], r["dt"], r["strategy"])
            bucket = picked.setdefault(key, {})
            for x in rows:
                c = canon(x["params"])
                if c in bucket:
                    continue
                if len(bucket) >= limit_per:
                    break
                bucket[c] = {"params": json.loads(c), "было": float(x["net_profit"]),
                             "сделок": int(x["total_trades"]), "run": r["id"]}
        return picked
    finally:
        await pool.close()


def build_jobs(picked: dict) -> tuple[list, list]:
    jobs, skipped = [], []
    for (sym, df, dt_, strat), bucket in sorted(picked.items(), key=lambda k: str(k[0])):
        if not bucket:
            continue
        code = script_code(strat)
        if not code:
            skipped.append(f"{strat} ({len(bucket)} шт): код запуска неизвестен")
            continue
        sets = [dict(v["params"], symbol=sym) for v in bucket.values()]
        jobs.append({
            "campaign": f"camp-20260911-rck-{sym}-{df:%m%d}-{strat}"[:60],
            "scriptCode": code, "symbol": sym, "baseParams": {"symbol": sym},
            "dateFrom": f"{df:%Y-%m-%d}", "dateTo": f"{dt_:%Y-%m-%d}",
            "engine": "remote", "priority": PRIORITY, "paramSets": sets,
            "_was": {canon(v["params"]): v["было"] for v in bucket.values()},
        })
    return jobs, skipped


async def submit(jobs: list) -> None:
    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    seeds = {}
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=180) as cl:
        for j in jobs:
            was = j.pop("_was")
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                seeds[j["campaign"]] = {"symbol": j["symbol"], "from": j["dateFrom"],
                                        "to": j["dateTo"], "was": was}
                print(f"  поставлено {j['campaign']} (было {len(was)} строк)")
            else:
                print(f"  ошибка {r.status_code}: {r.text[:160]}")
    old = {}
    if os.path.exists(SEEDS):
        old = json.load(open(SEEDS, encoding="utf-8"))
    old.update(seeds)
    json.dump(old, open(SEEDS, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"поставлено {len(seeds)} заданий, посевы: {SEEDS}")


async def report() -> int:
    if not os.path.exists(SEEDS):
        print(f"нет файла посевов {SEEDS}")
        return 1
    seeds = json.load(open(SEEDS, encoding="utf-8"))
    s = Settings()
    pool = await asyncpg.create_pool(s.lab_db_url, min_size=1, max_size=2)
    same = washed = 0
    try:
        for camp, seed in sorted(seeds.items()):
            rows = await pool.fetch(
                "SELECT b.params, b.net_profit FROM backtest_results b"
                " JOIN backtest_runs r ON r.id = b.run_id"
                " WHERE r.id LIKE $1 AND r.status='done' AND b.net_profit IS NOT NULL",
                f"%{camp[5:]}%")
            if not rows:
                print(f"{camp}: ещё не посчитано")
                continue
            got = {canon(x["params"]): float(x["net_profit"]) for x in rows}
            print(f"{camp} ({seed['from']}..{seed['to']}):")
            for c, was in sorted(seed["was"].items(), key=lambda kv: -kv[1]):
                if c not in got:
                    print(f"   {was:+12,.0f} -> нет строки")
                    continue
                new = got[c]
                flag = "совпало" if abs(new - was) <= max(1.0, abs(was) * 0.02) else "ПРОСЕЛО"
                if flag == "совпало":
                    same += 1
                else:
                    washed += 1
                print(f"   {was:+12,.0f} -> {new:+12,.0f}  {flag}")
        print(f"\nитого: совпало {same}, просело {washed}")
    finally:
        await pool.close()
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-per", type=int, default=6, help="верхних строк на кампанию")
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        return await report()

    picked = await collect(args.limit_per, args.min_trades, args.days)
    jobs, skipped = build_jobs(picked)
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"кампаний с обрезанным окном: {len(picked)} | заданий: {len(jobs)} | "
          f"строк: {total}")
    for j in jobs:
        print(f"  {j['symbol']:<3} {j['dateFrom']}..{j['dateTo']} "
              f"{j['campaign'].rsplit('-', 1)[-1]:<22} {len(j['paramSets'])} шт")
    for s_ in skipped:
        print(f"  пропуск: {s_}")
    if not args.submit:
        print("Сухой прогон — ничего не поставлено.")
        return 0
    await submit(jobs)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
