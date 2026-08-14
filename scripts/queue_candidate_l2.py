"""Уровень 2 гейта кандидата: проверки, которых нет в лидерборде.

Уровень 1 (`candidate_gate.py`) судит по УЖЕ посчитанным строкам. Пройти его
может и строка, которую считал другой билд library.py, и строка, живущая только
на своём контракте и своей стороне рынка. Поэтому кандидат обязан пережить ещё
три прогона ТЕКУЩИМ кодом, и все три ставятся здесь одной пачкой:

  ВНУТРИ ОКНА   тот же период отбора, оба контракта. Совпало с лидербордом ->
                строку считал текущий код; разошлось -> строка история, не оценка.
  ЗЕРКАЛО СТОРОН allow_long/allow_short 1/0 и 0/1 против 1/1. Конфиг, весь плюс
                которого сделан одной стороной, держится не на стратегии, а на
                тренде инструмента в окне отбора.
  ВНЕ ОКНА      непрерывный контракт на периоде, который в отборе не участвовал.

Отдельный прогон вместо расширения кампании: сетка тут не нужна, нужны точные
конфиги, а `paramSets` обходит произведение осей и локальный потолок комбо.

ЗАПУСК НА ХОСТЕРЕ (ISS с dev-бокса недоступна):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_candidate_l2.py --gate-json /tmp/gate_pass.json --dry-run
    PYTHONPATH=. $PY scripts/queue_candidate_l2.py --gate-json /tmp/gate_pass.json --submit
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

# Окно отбора кампании camp-20260810-honest. Совпадение с ним и есть проверка
# «строку считал текущий код».
IN_FROM, IN_TO = "2026-05-04", "2026-08-08"

# Вне окна: непрерывная склейка ближнего контракта. Экспирационные BRU6/BRQ6
# зимой были тонким дальним месяцем, поэтому OOS идёт по базовому коду.
OOS_FROM, OOS_TO = "2026-02-01", "2026-05-03"

SIDES = [(1, 1), (1, 0), (0, 1)]


def script_code(sid: str) -> str:
    return f"from trader.lab.strategies.library import make_on_bar\non_bar = make_on_bar('{sid}')"


def core_key(p: dict) -> tuple:
    """Ядро конфига: то, что задаёт сигнал и лестницу, без сторон и символа."""
    keys = ("period", "mult", "tp_atr", "avg_max", "avg_step_atr", "avg_atr_n",
            "min_gap_atr", "min_gap_pts", "sl_pct", "sl_frac", "cooldown_min",
            "cooldown_pct", "dv_bars", "dv_range_pts")
    return tuple((k, float(p.get(k) or 0)) for k in keys)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-json", required=True, help="вывод candidate_gate.py --json")
    ap.add_argument("--top", type=int, default=5, help="сколько уникальных конфигов брать")
    ap.add_argument("--symbols", default="BRU6,BRQ6", help="контракты для прогона внутри окна")
    ap.add_argument("--oos-symbol", default="BR",
                    help="контракт(ы) вне окна, через запятую. Непрерывная склейка "
                         "есть не везде: у BR за февраль-май 2026 она даёт 13 сделок "
                         "за три месяца — это отсутствие данных, а не отказ конфига, "
                         "и такой прогон НИЧЕГО не проверяет. Проверять надо на "
                         "экспирационном контракте того периода.")
    ap.add_argument("--only-oos", action="store_true",
                    help="только прогон вне окна (внутри окна уже посчитан)")
    ap.add_argument("--oos-sides", action="store_true",
                    help="вне окна прогнать и зеркало сторон: одностороннее "
                         "преимущество обязано подтвердиться на ДРУГОМ периоде, "
                         "иначе это тренд инструмента, а не стратегия")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    rows = json.load(open(args.gate_json))
    if not rows:
        raise SystemExit("гейт не отдал ни одной строки")

    # Один конфиг может пройти гейт на обоих контрактах — берём лучший по net,
    # иначе половина прогона уйдёт на дубликаты.
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["strategy"], core_key(r["params"]))
        if k not in best or r["net"] > best[k]["net"]:
            best[k] = r
    picked = sorted(best.values(), key=lambda x: -x["net"])[:args.top]

    symbols = [s for s in args.symbols.split(",") if s]
    jobs = []
    for i, r in enumerate(picked, 1):
        strat = r["strategy"]
        base = {k: v for k, v in r["params"].items()}
        base["qty"] = 1                       # qty только масштабирует P&L
        code = script_code(strat)
        if not args.only_oos:
            for sym in symbols:
                b = dict(base, symbol=sym)
                jobs.append({"campaign": f"candl2in{i}", "scriptCode": code, "symbol": sym,
                             "baseParams": b, "dateFrom": IN_FROM, "dateTo": IN_TO,
                             "engine": "remote",
                             "paramSets": [{"allow_long": lo, "allow_short": sh}
                                           for lo, sh in SIDES]})
        oos_sets = SIDES if args.oos_sides else [(1, 1)]
        for sym in args.oos_symbol.split(","):
            if not sym:
                continue
            b = dict(base, symbol=sym)
            jobs.append({"campaign": f"candl2oos{i}", "scriptCode": code,
                         "symbol": sym, "baseParams": b,
                         "dateFrom": OOS_FROM, "dateTo": OOS_TO, "engine": "remote",
                         "paramSets": [{"allow_long": lo, "allow_short": sh}
                                       for lo, sh in oos_sets]})

    combos = sum(len(j["paramSets"]) for j in jobs)
    print(f"конфигов {len(picked)} | заданий {len(jobs)} | комбо {combos}")
    for r in picked:
        p = r["params"]
        print(f"  {r['strategy']:14s} {r['symbol']:5s} net {r['net']:>9,.0f} "
              f"сделок {r['trades']:>4} | period {p.get('period')} mult {p.get('mult')} "
              f"tp {p.get('tp_atr')} avg_max {p.get('avg_max')} sl_pct {p.get('sl_pct')}")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                ok += 1
            else:
                err += 1
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}, ошибок {err}, комбо {combos}")


if __name__ == "__main__":
    main()
