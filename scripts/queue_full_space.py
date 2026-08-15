"""Полное пространство ОДНОЙ стратегии вместе с райдерами, сразу на двух контрактах.

Гипотеза оператора 15.08.2026: все прошлые прогоны брали базу из хитпарада, а её
подбирали БЕЗ райдеров. Райдеры меняют управление позицией целиком — значит с
ними может выиграть совсем другая база, которую прежний отбор никогда не видел.

Поэтому здесь перебираются РАЗОМ: периоды сигнала, весь слой управления позицией
и оба райдера. Осей два десятка, полный продукт астрономичен, поэтому берётся
СЛУЧАЙНАЯ ВЫБОРКА с фиксированным зерном — воспроизводимая и без повторов.

Три отсева ещё до постановки в очередь, каждый оплачен прошлым опытом:
  fast < mid < slow  — равные периоды это вырожденность: скользящие не
                       пересекаются, знак залипает навсегда, и в хитпараде такие
                       строки выглядят как net в сотни тысяч при нулевой просадке;
  прогрев <= 600     — столько закрытых баров помнит раннер. Строка, которой надо
                       больше, в бою слепа часами после каждого рестарта;
  одна и та же выборка на ОБА контракта — чтобы «работает на обоих» можно было
                       спросить сразу, а не вторым заходом. Именно этого не
                       хватило первому заходу по издержкам, и он дал подгонку.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/queue_full_space.py --strategy triple_sma --n 960 --submit
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

TARGETS = [("RIU6", "2026-05-04", "2026-08-08"), ("RIM6", "2026-02-01", "2026-06-18")]

# Значения осей. Не непрерывные диапазоны, а осмысленные ступени: выборка из
# сплошного диапазона тратит прогоны на неразличимые соседние значения.
GRID = {
    "fast": [2, 3, 5, 8, 12, 18, 25],
    "mid": [10, 15, 20, 30, 40, 60],
    "slow": [30, 50, 80, 120, 160, 200],
    "avg_max": [1, 2, 4, 7, 10],
    "avg_step_atr": [0, 8, 14, 21, 30],
    "tp_atr": [0, 20, 40, 60],
    "sl_frac": [0, 50, 100],
    "sl_pct": [0, 50, 100],
    "avg_atr_n": [10, 14, 25, 40],
    "min_gap_atr": [0, 10, 20],
    "cooldown_min": [0, 60, 240],
    "cooldown_pct": [0, 1],
    "dv_bars": [0, 60],
    "dv_range_pts": [0, 300, 600],
    "allow_long": [0, 1],
    "allow_short": [0, 1],
    # райдер 1: эскалация после убытка
    "bet_step": [0, 1, 2],
    "bet_max": [5, 10],
    "super_y": [0, 1, 2],
    "super_z": [1, 2, 3],
    "k_avg": [10, 15, 20, 30],
}
# райдер 2: расписание сторон. Границы и маски идут ПАРОЙ, иначе выборка
# насыпет бессмысленных сочетаний вроде «конец второго окна раньше первого».
TOD = [(0, 0, 3, 3, 3),                      # выключено
       (600, 1080, 3, 2, 1), (600, 1080, 1, 2, 3), (600, 1080, 2, 3, 2),
       (671, 1140, 3, 2, 1), (671, 1140, 2, 3, 3), (600, 1080, 2, 3, 3),
       (540, 1140, 1, 2, 3), (660, 1200, 3, 2, 1)]


def sample(sid: str, n: int, seed: int) -> list[dict]:
    spec = REGISTRY[sid]
    rnd = random.Random(seed)
    seen: set[tuple] = set()
    out: list[dict] = []
    guard = 0
    while len(out) < n and guard < n * 200:
        guard += 1
        p = {k: rnd.choice(v) for k, v in GRID.items()}
        if not (p["fast"] < p["mid"] < p["slow"]):
            continue                     # вырожденные периоды не берём вовсе
        if not (p["allow_long"] or p["allow_short"]):
            continue                     # оба нуля = робот не торгует
        m1, m2, s1, s2, s3 = rnd.choice(TOD)
        p.update(tod_m1=m1, tod_m2=m2, tod_s1=s1, tod_s2=s2, tod_s3=s3)
        try:
            if int(spec["warmup"]({**spec["default_params"], **p})) > RUNNER_BARS:
                continue                 # в память раннера не влезет
        except Exception:                # noqa: BLE001 — битую комбинацию просто мимо
            continue
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="triple_sma")
    ap.add_argument("--n", type=int, default=960)
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    sid = args.strategy
    if sid not in REGISTRY:
        raise SystemExit(f"нет такой стратегии: {sid}")
    sets = sample(sid, args.n, args.seed)
    chunks = [sets[i:i + CHUNK] for i in range(0, len(sets), CHUNK)]
    print(f"{sid}: комбинаций {len(sets)} × контрактов {len(TARGETS)} = "
          f"{len(sets) * len(TARGETS)} прогонов, заданий {len(chunks) * len(TARGETS)}")
    if args.dry_run or not args.submit:
        print("сухой прогон, ничего не отправлено")
        return

    code = (f"from trader.lab.strategies.library import make_on_bar\n"
            f"on_bar = make_on_bar('{sid}')")
    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    ok = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"},
                      timeout=60) as cl:
        for sym, a, b in TARGETS:
            for i, part in enumerate(chunks, 1):
                r = cl.post("/api/v1/backtest/run", json={
                    "campaign": f"full{sid[:6]}{sym[:4].lower()}{i}", "scriptCode": code,
                    "symbol": sym, "baseParams": {"symbol": sym, "qty": 1},
                    "dateFrom": a, "dateTo": b, "engine": "remote",
                    "priority": 100, "paramSets": part})
                ok += r.status_code in (200, 201, 202)
    print(f"поставлено {ok} заданий")


if __name__ == "__main__":
    main()
