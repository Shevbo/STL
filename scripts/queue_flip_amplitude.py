"""Прогон чувствительности: амплитудный фильтр разворота macd_shectory1.

Инцидент 09.09.2026: agent-macdshort-RIU6-v1 и lxk22tsffsxiiotb8kmpsato
синхронно перевернулись по ложному MACD-кроссоверу на пике, один флип = −22k
gross. Введён flip_min_pts (см. library.py). Здесь — прогон живых конфигов
ОБОИХ роботов поверх оси flip_min_pts, чтобы увидеть net/просадку с порогом
1750 и рядом, а не брать 1750 на веру.

Дважды служит и пробником оси на i9: flip_min_pts=0 и большой порог обязаны
дать РАЗНЫЕ net/сделки. Совпали до рубля -> i9 крутит старый library.py
(сверить lib_sha в i9_heartbeat с sha256sum trader/lab/strategies/library.py).

ЗАПУСК НА ХОСТЕРЕ:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/queue_flip_amplitude.py --dry-run
    PYTHONPATH=. $PY scripts/queue_flip_amplitude.py --submit
"""
from __future__ import annotations

import argparse
import os

import httpx

from trader.auth.portal import make_session_token

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"

CODE = ("from trader.lab.strategies.library import make_on_bar\n"
        "on_bar = make_on_bar('macd_shectory1')")

# Порог хода от средней входа, при котором разворот по сигналу исполняется.
# 0 = фильтр выключен (прежнее поведение и одновременно опора пробника оси).
FLIP_PTS = [0, 700, 1050, 1400, 1750, 2100, 2625, 3500]

# Живые конфиги на момент инцидента (mirror params_json), bar_offset_min -> 0:
# в бэктесте бары штампованы МСК-как-UTC, поправка 180 сдвинула бы tod-расписание.
BASE_COMMON = dict(
    qty=1, fast=57, slow=48, signal=10, avg_atr_n=25, avg_step_atr=21,
    min_gap_pts=0, cooldown_min=0, cooldown_pct=1, nd_days=5, gap_auto=0,
    sl_frac=0, sl_pct=100, dv_bars=60, dv_range_pts=300,
    tod_m1=600, tod_m2=1080, tod_s1=3, tod_s2=2, tod_s3=1, bar_offset_min=0,
)
BASELINES = {
    "macdshort": dict(BASE_COMMON, avg_max=10, tp_atr=40, k_avg=10,
                      allow_long=0, allow_short=1),
    "lxk22": dict(BASE_COMMON, avg_max=20, tp_atr=80, k_avg=20,
                  allow_long=1, allow_short=1,
                  bet_step=2, bet_max=10, super_y=2, super_z=2),
}

# Основной контракт + 2-й (истёкший ближний месяц) на его собственном окне —
# та же стратегия на другом контракте и другом периоде = проверка воспроизводимости.
RUNS = [
    ("RIU6", "2026-06-09", "2026-09-09"),
    ("RIM6", "2026-03-16", "2026-06-08"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    jobs = []
    for name, base in BASELINES.items():
        for sym, d_from, d_to in RUNS:
            jobs.append({
                "campaign": f"flipamp-{name}-{sym}",
                "scriptCode": CODE, "symbol": sym,
                "baseParams": dict(base, symbol=sym),
                "dateFrom": d_from, "dateTo": d_to, "engine": "remote",
                "paramSets": [{"flip_min_pts": v} for v in FLIP_PTS],
            })
    combos = sum(len(j["paramSets"]) for j in jobs)
    print(f"заданий {len(jobs)} | комбо {combos} | ось flip_min_pts {FLIP_PTS}")
    for j in jobs:
        print(f"  {j['campaign']:22s} {j['dateFrom']}..{j['dateTo']}")
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
