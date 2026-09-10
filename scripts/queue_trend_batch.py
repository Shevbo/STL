"""Перебор трендовых standalone (supertrend, donchian) на i9.

ЗАЧЕМ. swing_trend — первый механизм нового класса (низкая частота, qty=1,
симметричный, зеркальный гейт). Здесь тот же класс расширяется двумя трендовыми
standalone, давно лежащими в репозитории, но никогда не гонявшимися
систематически на IN/OUT с зеркалом:
  supertrend  — ATR-тренд, всегда в рынке, long+short (симметричный).
  donchian    — пробой канала (Turtle), long-only; invert даёт short-only зеркало.
Обе читают invert=1 (добавлено в стратегии) для проверки «механизм, а не сторона».

Окна/символы/зеркало — как у swing_trend, чтобы к утру был честный ответ
«живёт ли победитель вне окна подгонки» одним прогоном:
  IN  2025-04-01..2025-12-31  — подгонка (9 месяцев)
  OUT 2026-01-01..2026-09-08  — вне выборки (8 месяцев, включая падение 2026)

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_trend_batch.py --dry-run
    PYTHONPATH=. $PY scripts/queue_trend_batch.py --submit
"""
from __future__ import annotations

import argparse
import itertools
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

SYMBOLS = ["RI", "Si"]
WINDOWS = [
    ("in",  "2025-04-01", "2025-12-31"),
    ("out", "2026-01-01", "2026-09-08"),
]

STRATEGIES = {
    "supertrend": {
        "code": "from trader.lab.strategies.supertrend import on_bar, on_start, on_stop",
        "axes": {
            "atr_period": [7, 14, 21, 40, 60, 90],
            "multiplier": [15, 20, 25, 30, 40, 50],
        },
    },
    "donchian": {
        "code": "from trader.lab.strategies.donchian_breakout import on_bar, on_start, on_stop",
        "axes": {
            "entry_period": [10, 20, 40, 60, 80, 120, 160],
            "exit_period": [3, 5, 8, 12, 20],
        },
    },
}
PIN = dict(qty=1, allow_long=1, allow_short=1, bar_offset_min=0)


def combos_for(strategy: str) -> list[dict]:
    axes = STRATEGIES[strategy]["axes"]
    keys = list(axes)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*axes.values())]
    if strategy == "donchian":
        # M < N по смыслу системы; вырожденные M >= N не гоняем
        combos = [c for c in combos if c["exit_period"] < c["entry_period"]]
    return combos


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    jobs = []
    for strategy, meta in STRATEGIES.items():
        combos = combos_for(strategy)
        for sym in SYMBOLS:
            for win, d_from, d_to in WINDOWS:
                for inv in (0, 1):
                    jobs.append({
                        "campaign": f"trendbatch-{strategy}-{sym}-{win}-inv{inv}",
                        "scriptCode": meta["code"], "symbol": sym,
                        "baseParams": dict(PIN, symbol=sym, invert=inv),
                        "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                        "priority": 40,
                        "paramSets": combos,
                    })
    total = sum(len(j["paramSets"]) for j in jobs)
    print(f"стратегий {len(STRATEGIES)} | символов {len(SYMBOLS)} | окна {len(WINDOWS)} "
          f"| заданий {len(jobs)} | комбо {total}")
    for j in jobs:
        print(f"  {j['campaign']:40s} {j['dateFrom']}..{j['dateTo']}  {len(j['paramSets'])} шт")
    if not args.submit or args.dry_run:
        print("сухой прогон, ничего не отправлено")
        return

    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = err = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=120) as cl:
        for j in jobs:
            r = cl.post("/api/v1/backtest/run", json=j)
            if r.status_code in (200, 201, 202):
                ok += 1
            else:
                err += 1
                print(f"  ошибка {r.status_code}: {r.text[:200]}")
    print(f"поставлено {ok}, ошибок {err}, комбо {total}")


if __name__ == "__main__":
    main()
