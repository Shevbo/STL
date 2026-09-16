"""Смягчённый гейт: кто в плюсе НА ТЕКУЩЕМ ОКНЕ с ИЗМЕРЕННЫМИ издержками (16.09.2026).

Запрос оператора: смягчить требования и пробовать в реал малым объёмом тех, кто на
текущем окне в плюсе. Скан считает по каждой стратегии реестра и каждому ТФ:
  net_now   — нетто за последние --months месяцев,
  net_prev  — нетто за предыдущее окно такой же длины (плюс был всегда или только что),
  dd        — максимальная просадка MTM внутри текущего окна,
  контрактов, филлов.

Издержки ПОИНСТРУМЕНТНО и по факту, а не по предположению (замер 16.09 по архиву):
полспреда RI 5.0, GD 0.2, Si 1.0, GZ 1.0 пт — медианы; сбор тейкера по группе MOEX.
Раньше в прогонах по GD стояло 0.05, то есть издержки были занижены вчетверо.

СТРАХОВКА ОТ САМООБМАНА. Доля положительных ячеек печатается отдельно: если в плюсе
половина реестра, это дрейф инструмента, а не стратегия (на RI гейт проходили 66%
случайных трендовых правил).

    PYTHONPATH=. python scripts/window_scan.py --txt GD.txt --fee 0.000132 --half 0.2 --months 3
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

SKIP = {"macd_shectory1", "bollinger_bo_m1", "williams_r"}


def _run(rid, tf, contract, bars, fee, half):
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(rid)
    params = {**REGISTRY[rid]["default_params"], "bet_step": 0, "symbol": contract, "flatten_end": 1}
    r = asyncio.run(run_single_backtest(mod, bars, contract, params))
    close = {datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%d"): b.close for b in bars}
    days = {}
    fills = 0
    for d, (pnl, n) in daily_mtm(bars, r["trades"]).items():
        days[d] = pnl - n * (fee * close[d] + half)
        fills += n
    return rid, tf, contract, days, fills


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", required=True)
    ap.add_argument("--fee", type=float, required=True)
    ap.add_argument("--half", type=float, required=True)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--point", type=float, default=1.0, help="₽ за пункт, для колонки в рублях")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    by = load_ri(a.txt)
    last_day = max(max(datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%d")
                       for b in bars) for bars in by.values())
    y, m, _ = (int(x) for x in last_day.split("-"))
    def shift(months):
        mm = m - months
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        return f"{yy:04d}-{mm:02d}-01"
    cur_from, prev_from = shift(a.months), shift(2 * a.months)
    print(f"{a.txt}: последний день {last_day}; текущее окно с {cur_from}, предыдущее с {prev_from}")

    jobs = [(rid, tf, c) for rid in REGISTRY if rid not in SKIP for tf in (15, 60) for c in by]
    bars_by = {tf: {c: aggregate(b, tf) for c, b in by.items()} for tf in (15, 60)}
    now, prev, fills, cons = (defaultdict(float), defaultdict(float),
                              defaultdict(int), defaultdict(set))
    dd_series = defaultdict(list)
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(_run, rid, tf, c, bars_by[tf][c], a.fee, a.half) for rid, tf, c in jobs]
        for i, fu in enumerate(as_completed(futs), 1):
            rid, tf, c, days, n = fu.result()
            k = (rid, tf)
            for d, v in days.items():
                if d >= cur_from:
                    now[k] += v
                    dd_series[k].append((d, v))
                    if abs(v) > 0:
                        cons[k].add(c)
                elif d >= prev_from:
                    prev[k] += v
            fills[k] += n
            if i % 100 == 0:
                print(f"{i}/{len(jobs)}", flush=True)

    def maxdd(k):
        eq = peak = dd = 0.0
        for _, v in sorted(dd_series[k]):
            eq += v
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        return dd

    rows = sorted(now, key=lambda k: -now[k])
    pos = sum(now[k] > 0 for k in rows)
    print(f"\nв плюсе на текущем окне {pos} ячеек из {len(rows)} = {pos / len(rows):.0%} "
          f"(половина = дрейф инструмента, а не стратегии)")
    print(f"{'стратегия':15} {'ТФ':>3} {'сейчас':>9} {'₽':>10} {'прошлое':>9} {'просадка':>9} "
          f"{'контр':>6} {'оба+':>5}")
    for k in rows:
        rid, tf = k
        print(f"{rid:15} M{tf:<2} {now[k]:9.0f} {now[k] * a.point:10.0f} {prev[k]:9.0f} "
              f"{maxdd(k):9.0f} {fills[k]:6} {'да' if now[k] > 0 and prev[k] > 0 else '':>5}")


if __name__ == "__main__":
    main()
