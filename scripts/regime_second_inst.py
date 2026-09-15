"""keltner_bo на втором инструменте (BR, GD) — протокол из проверки fable 15.09.2026.

На RI 2022-26 гейт проходят 66% случайных трендовых правил; keltner_bo M15 по умолчанию
лучше 198/200 плацебо, но с поправкой на отбор (лучший из 18) не значим. Семейство
keltner M15 на случайных параметрах — плато (16/17 прошли). Проверяем на инструментах,
не коррелированных с RI.

КРИТЕРИЙ (фиксируется в коммите ДО прогона, на каждом инструменте отдельно; линия
живёт, только если пройдены ОБА инструмента и на каждом (a) И (b)):
  (a) фиксированный вектор: keltner_bo M15 с параметрами по умолчанию — нетто полной
      серии выше p95 плацебо того же инструмента (все розыгрыши всех ячеек), И
      нетто минус лучший контракт > 0, И нетто > 0 минимум в 3 из 5 лет;
  (b) семейство: 30 розыгрышей на ячейку (9 трендовых стратегий × {M15, H1}, seed
      20260916), keltner_bo M15 — ранг 1 из 9 стратегий на M15 по медиане нетто
      ячейки, И гейт (нетто > 0 на 2022-24 и 2025-26, > 0 в >= 3 из 5 лет,
      >= 200 контрактов) проходят >= 50% розыгрышей ячейки.
Никаких вырезок контрактов. Издержки поинструментно: --fee (доля номинала, тейкер) и
--half (полспреда в пунктах цены).

    PYTHONPATH=. python scripts/regime_second_inst.py --txt GD.txt --fee 0.000132 --half 0.05
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import median

sys.path.insert(0, "scripts")
from regime_step0 import aggregate, daily_mtm, load_ri  # noqa: E402
from regime_tf_placebo import TREND, draw  # noqa: E402

from trader.lab.backtest import run_single_backtest  # noqa: E402
from trader.lab.strategies.library import REGISTRY, make_on_bar  # noqa: E402

DRAWS_PER_CELL = 30
SEED = 20260916


def cell_draws(rng: random.Random, rid: str, tf: int, n: int) -> list[dict]:
    out = []
    while len(out) < n:                      # draw() выбирает стратегию и ТФ сам — берём подходящие
        r, _tf, p = draw(rng)
        if r == rid:
            out.append(p)
    return out


def _run(k, rid, contract, bars, p, fee, half):
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(rid)
    params = {**REGISTRY[rid]["default_params"], "bet_step": 0, **p, "symbol": contract, "flatten_end": 1}
    r = asyncio.run(run_single_backtest(mod, bars, contract, params))
    close = {datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%d"): b.close for b in bars}
    yr, fills, net = defaultdict(float), 0, 0.0
    for d, (pnl, n) in daily_mtm(bars, r["trades"]).items():
        v = pnl - n * (fee * close[d] + half)
        yr[d[:4]] += v
        net += v
        fills += n
    return k, contract, dict(yr), fills, net


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", required=True)
    ap.add_argument("--fee", type=float, required=True)
    ap.add_argument("--half", type=float, required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--per-cell", type=int, default=DRAWS_PER_CELL)
    a = ap.parse_args()

    m1 = load_ri(a.txt)
    bars = {tf: {c: aggregate(b, tf) for c, b in m1.items()} for tf in (15, 60)}
    del m1
    rng = random.Random(SEED)
    jobs = [("default", "keltner_bo", 15, {})]
    for rid in TREND:
        for tf in (15, 60):
            jobs += [(f"{rid}:{tf}", rid, tf, p) for p in cell_draws(rng, rid, tf, a.per_cell)]

    yr = defaultdict(lambda: defaultdict(float))
    fills, by_con = defaultdict(int), defaultdict(dict)
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(_run, k, rid, c, bars[tf][c], p, a.fee, a.half)
                for k, (_, rid, tf, p) in enumerate(jobs) for c in bars[tf]]
        for i, fu in enumerate(as_completed(futs), 1):
            k, c, y, n, net = fu.result()
            for key, v in y.items():
                yr[k][key] += v
            fills[k] += n
            by_con[k][c] = net
            if i % 1000 == 0:
                print(f"{i}/{len(futs)}", flush=True)

    def stats(k):
        y = yr[k]
        tot = sum(y.values())
        a1 = sum(v for key, v in y.items() if key <= "2024")
        a2 = sum(v for key, v in y.items() if key >= "2025")
        gate = a1 > 0 and a2 > 0 and sum(v > 0 for v in y.values()) >= 3 and fills[k] >= 200
        return tot, gate, tot - max(by_con[k].values()), sum(v > 0 for v in y.values())

    d_tot, _, d_rob, d_yrs = stats(0)
    placebo = sorted(stats(k)[0] for k in range(1, len(jobs)))
    p95 = placebo[int(0.95 * len(placebo))]
    ok_a = d_tot > p95 and d_rob > 0 and d_yrs >= 3
    print(f"(a) keltner M15 дефолт: нетто {d_tot:+.0f}, p95 плацебо {p95:+.0f}, минус лучший контракт {d_rob:+.0f}, "
          f"лет+ {d_yrs} -> {'ДА' if ok_a else 'НЕТ'}")

    cells = defaultdict(list)
    for k in range(1, len(jobs)):
        cells[jobs[k][0]].append(stats(k))
    print("(b) ячейки: медиана нетто / доля прошедших гейт")
    for tf in (15, 60):
        rows = sorted(((median(t for t, *_ in cells[f"{rid}:{tf}"]), sum(g for _, g, *_ in cells[f"{rid}:{tf}"])
                        / len(cells[f"{rid}:{tf}"]), rid) for rid in TREND), reverse=True)
        print(f"  M{tf}: " + "; ".join(f"{rid} {m:+.0f}/{s:.0%}" for m, s, rid in rows))
        if tf == 15:
            rank = [rid for *_, rid in rows].index("keltner_bo") + 1
            share = dict((rid, s) for _, s, rid in rows)["keltner_bo"]
            ok_b = rank == 1 and share >= 0.5
            print(f"  keltner M15 ранг {rank}/9, прошли {share:.0%} -> {'ДА' if ok_b else 'НЕТ'}")
    print("ИНСТРУМЕНТ:", "ПРОЙДЕН" if ok_a and ok_b else "НЕ ПРОЙДЕН")


if __name__ == "__main__":
    main()
