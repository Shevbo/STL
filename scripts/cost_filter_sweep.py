"""Фильтр издержек (cost_atr): сколько он режет и что оставляет (17.09.2026).

Запрос оператора: фильтр от частых сделок, которые в основном едят комиссию. Механика —
в make_on_bar: вход разрешён, только если ATR не меньше cost_atr/10 цены круга, где круг
= 2 × (полспреда + биржевой сбор тейкера в пунктах). Гейтит только входы.

ЗАЧЕМ ЗАМЕР. Ещё один параметр — это ещё одна ось подгонки. Поэтому «помогает» значит
одно: нетто выросло И на 2022-2024, И на нетронутых 2025-2026. Улучшение на одной
половине — шум. Порог 0 = выключено, это база сравнения.

Издержки измеренные (архив 16-17.09): полспреда RI 5 пт; сбор тейкера считает движок.

    PYTHONPATH=. python scripts/cost_filter_sweep.py --ri RI.txt --tf 1 --tf 15
"""
from __future__ import annotations

import argparse
import asyncio
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

from trader.lab.backtest import run_single_backtest
from trader.lab.strategies.library import REGISTRY, make_on_bar
from scripts.regime_step0 import aggregate, daily_mtm, load_ri

FEE_RATE = 0.000066          # доля номинала, тейкер RI (commission.py)
HALF = 5.0                   # полспреда RI, пунктов (медиана по архиву)
STRATS = ("macd_cross", "stochastic", "momentum", "cci", "keltner_bo")
GRID = (0, 5, 10, 20, 40)    # cost_atr: 0=выкл, 10 = ATR >= цены круга


def _run(rid, tf, contract, bars, cost_atr):
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(rid)
    params = {**REGISTRY[rid]["default_params"], "bet_step": 0, "symbol": contract,
              "flatten_end": 1, "cost_atr": cost_atr, "spread_pts": HALF}
    r = asyncio.run(run_single_backtest(mod, bars, contract, params))
    close = {datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%d"): b.close for b in bars}
    out, fills = defaultdict(float), 0
    for d, (pnl, n) in daily_mtm(bars, r["trades"]).items():
        out[d] += pnl - n * (FEE_RATE * close[d] + HALF)
        fills += n
    return rid, tf, cost_atr, dict(out), fills


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ri", required=True)
    ap.add_argument("--tf", type=int, action="append", default=None)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    tfs = a.tf or [1, 15]

    by = load_ri(a.ri)
    bars_by = {tf: {c: aggregate(b, tf) for c, b in by.items()} for tf in tfs}
    jobs = [(rid, tf, c, cost) for rid in STRATS for tf in tfs for c in by for cost in GRID]
    print(f"контрактов {len(by)}, прогонов {len(jobs)}", flush=True)

    net = defaultdict(lambda: defaultdict(float))
    fills = defaultdict(int)
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(_run, rid, tf, c, bars_by[tf][c], cost) for rid, tf, c, cost in jobs]
        for i, fu in enumerate(as_completed(futs), 1):
            rid, tf, cost, days, n = fu.result()
            for d, v in days.items():
                net[(rid, tf, cost)][d] += v
            fills[(rid, tf, cost)] += n
            if i % 100 == 0:
                print(f"{i}/{len(jobs)}", flush=True)

    print(f"\n{'стратегия':12} {'ТФ':>3} {'порог':>6} {'контрактов':>10} {'2022-24 ₽':>11} "
          f"{'2025-26 ₽':>11} {'итог ₽':>11} {'₽/контр ост.':>13} {'₽/контр убр.':>13} "
          f"{'лучше базы':>11}")
    for rid in STRATS:
        for tf in tfs:
            base = None
            for cost in GRID:
                d = net[(rid, tf, cost)]
                a1 = sum(v for k, v in d.items() if k[:4] <= "2024") * 1.68
                a2 = sum(v for k, v in d.items() if k[:4] >= "2025") * 1.68
                # КОНТРОЛЬ «отбор или просто меньше сделок»: при отрицательном ожидании
                # любое прореживание поднимает нетто, поэтому мало «нетто выросло».
                # Фильтр отбирает, только если ОСТАВЛЕННЫЕ сделки лучше УБРАННЫХ на
                # контракт (замечание fable 17.09: на спеке lxk22 при пороге 40
                # оставленные были ХУЖЕ убранных — это «торгуй реже», а не отбор).
                n = fills[(rid, tf, cost)]
                if cost == 0:
                    base = (a1, a2, n)
                    mark, kept, removed = "база", (a1 + a2) / n if n else 0.0, 0.0
                else:
                    mark = "ДА" if a1 > base[0] and a2 > base[1] else ""
                    kept = (a1 + a2) / n if n else 0.0
                    rm_n = base[2] - n
                    removed = ((base[0] + base[1]) - (a1 + a2)) / rm_n if rm_n else 0.0
                print(f"{rid:12} M{tf:<2} {cost:6} {n:10} {a1:11.0f} {a2:11.0f} "
                      f"{a1 + a2:11.0f} {kept:13.1f} {removed:13.1f} {mark:>11}")


if __name__ == "__main__":
    main()
