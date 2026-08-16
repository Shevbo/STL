"""Подбор параметров на РАСТУЩЕМ рынке — зеркало текущего падения.

Заказ оператора 15.08.2026. Весь день отбор шёл на падающих окнах и потому
выдавал шортовые конфиги: 7 из 9 выживших полного перебора triple_sma торговали
только в шорт, лучшие маски расписания ставили шорт в середину дня, а живой
конфиг на растущих окнах теряет. Прежде чем говорить о преимуществе стратегии,
надо увидеть, что вообще работает НА РОСТЕ.

ОКНА подобраны зеркально текущему падению по амплитуде:
  падение (сейчас): RIU6 2026-05-04..08-08, −20.9% за 96 дней
  рост (зеркало):   RIH6 2025-11-17..2026-01-25, +19.9% за 69 дней
  рост (второй):    RIZ5 2025-11-17..12-18, +10.2% — второй контракт того же
                    подъёма, чтобы сразу спросить «работает на обоих».

Перебирается то же пространство, что и на падении: сигнал, управление позицией и
оба райдера. Расписание сторон включено намеренно — если на росте выиграют
ЛОНГОВЫЕ маски, это и будет доказательством, что маска ловит направление рынка,
а не режим дня.
"""
from __future__ import annotations

import argparse
import os
import random

import httpx

from trader.auth.portal import make_session_token
from trader.lab.strategies.library import REGISTRY

API = "http://localhost:8000"
EMAIL = "bshevelev75@gmail.com"
RUNNER_BARS = 600
CHUNK = 32

RISING = [("RIH6", "2025-11-17", "2026-01-25"), ("RIZ5", "2025-11-17", "2025-12-18")]
# Те же окна падения, что и во всех прошлых проверках. Прогон ТОЙ ЖЕ выборки
# (зерно фиксировано) на них даёт matched pair: конфиг сравнивается сам с собой
# в другом режиме, а не с соседней строкой, отличной тремя параметрами.
FALLING = [("RIU6", "2026-05-04", "2026-08-08"), ("RIM6", "2026-02-01", "2026-06-18")]

GRID = {
    "fast": [12, 24, 36, 48, 57],
    "slow": [10, 20, 34, 48, 60],
    "signal": [5, 9, 14, 20],
    "avg_max": [1, 4, 10, 20],
    "avg_step_atr": [8, 14, 21, 30],
    "tp_atr": [0, 40, 80, 120],
    "sl_frac": [0, 50, 100],
    "sl_pct": [0, 100],
    "avg_atr_n": [14, 25, 40],
    "min_gap_atr": [0, 10, 20],
    "cooldown_min": [0, 120],
    "cooldown_pct": [1],
    "dv_bars": [0, 60],
    "dv_range_pts": [0, 300, 600],
    "allow_long": [0, 1],
    "allow_short": [0, 1],
    "nd_days": [5],
    "gap_auto": [0],
    "min_gap_pts": [0],
    "bet_step": [0, 2],
    "bet_max": [10],
    "super_y": [0, 2],
    "super_z": [2],
    "k_avg": [10, 20],
}
# Маски сторон: зеркальные пары намеренно. Если на росте победят ЛОНГОВЫЕ, это и
# есть доказательство, что маска ловит направление рынка, а не время суток.
TOD = [(0, 0, 3, 3, 3),
       (600, 1080, 3, 2, 1), (600, 1080, 1, 2, 3),
       (600, 1080, 3, 1, 2), (600, 1080, 2, 1, 3),
       (671, 1140, 1, 3, 1), (671, 1140, 2, 3, 2),
       (600, 1080, 1, 1, 3), (600, 1080, 2, 2, 3)]


def sample(sid: str, n: int, seed: int) -> list[dict]:
    spec = REGISTRY[sid]
    rnd = random.Random(seed)
    seen: set[tuple] = set()
    out: list[dict] = []
    guard = 0
    while len(out) < n and guard < n * 300:
        guard += 1
        p = {k: rnd.choice(v) for k, v in GRID.items()}
        # fast == slow делает линию MACD тождественно нулевой, а fast > slow даёт
        # вечный минус: обе вырожденности уже стоили нам восьми бумажных роботов.
        if p["fast"] <= p["slow"]:
            continue
        if not (p["allow_long"] or p["allow_short"]):
            continue
        m1, m2, s1, s2, s3 = rnd.choice(TOD)
        p.update(tod_m1=m1, tod_m2=m2, tod_s1=s1, tod_s2=s2, tod_s3=s3)
        try:
            if int(spec["warmup"]({**spec["default_params"], **p})) > RUNNER_BARS:
                continue
        except Exception:  # noqa: BLE001
            continue
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="macd_shectory1")
    ap.add_argument("--n", type=int, default=480)
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--mode", choices=("rising", "falling"), default="rising",
                    help="на каких окнах гонять ТУ ЖЕ выборку")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    sid = args.strategy
    targets = RISING if args.mode == "rising" else FALLING
    sets = sample(sid, args.n, args.seed)
    chunks = [sets[i:i + CHUNK] for i in range(0, len(sets), CHUNK)]
    print(f"{sid}: комбинаций {len(sets)} × окон {len(targets)} = "
          f"{len(sets) * len(targets)} прогонов, заданий {len(chunks) * len(targets)}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    code = (f"from trader.lab.strategies.library import make_on_bar\n"
            f"on_bar = make_on_bar('{sid}')")
    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for sym, a, b in targets:
            for i, part in enumerate(chunks, 1):
                r = cl.post("/api/v1/backtest/run", json={
                    "campaign": f"{args.mode[:4]}{sym[:4].lower()}{i}", "scriptCode": code,
                    "symbol": sym, "baseParams": {"symbol": sym, "qty": 1},
                    "dateFrom": a, "dateTo": b, "engine": "remote",
                    "priority": 100, "paramSets": part})
                ok += r.status_code in (200, 201, 202)
    print(f"поставлено {ok} заданий")


if __name__ == "__main__":
    main()
