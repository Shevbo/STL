"""«А сколько бы дало само правило?» — проверка смягчённого гейта на истории (16.09.2026).

Правило оператора: брать в работу то, что в плюсе на текущем окне. Проверяется прямо:
на каждом шаге отбираем ячейки (стратегия × ТФ), давшие плюс за прошедшее окно, и
считаем, сколько они дали на СЛЕДУЮЩЕМ окне — равными долями, по 1 лоту на ячейку.
Сравнение с двумя базами: весь реестр равными долями и «купил и держи» 1 лот.

Дневные ряды считаются один раз и кладутся в кэш CSV, дальше это арифметика.
Издержки поинструментно, измеренные по архиву (полспреда --half, сбор --fee).

    PYTHONPATH=. python scripts/rule_forward.py --txt GD.txt --fee 0.000132 --half 0.2 \
        --point 84.24 --cache gd.csv --months 3
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

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
    out = []
    for d, (pnl, n) in daily_mtm(bars, r["trades"]).items():
        out.append((f"{rid}:M{tf}", d, pnl - n * (fee * close[d] + half), n))
    return out


def build_cache(a) -> None:
    by = load_ri(a.txt)
    bars_by = {tf: {c: aggregate(b, tf) for c, b in by.items()} for tf in (15, 60)}
    jobs = [(rid, tf, c) for rid in REGISTRY if rid not in SKIP for tf in (15, 60) for c in by]
    with open(a.cache, "w", newline="", encoding="utf-8") as f, ProcessPoolExecutor(a.workers) as ex:
        w = csv.writer(f)
        w.writerow(["cell", "date", "net_pts", "fills"])
        futs = [ex.submit(_run, rid, tf, c, bars_by[tf][c], a.fee, a.half) for rid, tf, c in jobs]
        for i, fu in enumerate(as_completed(futs), 1):
            w.writerows(fu.result())
            if i % 100 == 0:
                print(f"{i}/{len(jobs)}", flush=True)


def read_cache(path):
    daily = defaultdict(lambda: defaultdict(float))
    fills = defaultdict(lambda: defaultdict(int))
    with open(path, encoding="utf-8") as f:
        next(f)
        for cell, d, v, n in csv.reader(f):
            daily[cell][d] += float(v)
            fills[cell][d] += int(n)
    return daily, fills


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", required=True)
    ap.add_argument("--fee", type=float, required=True)
    ap.add_argument("--half", type=float, required=True)
    ap.add_argument("--point", type=float, default=1.0)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--months", type=int, default=3, help="длина окна отбора и окна проверки")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    if not os.path.exists(a.cache):
        build_cache(a)
    daily, fills = read_cache(a.cache)
    cells = sorted(daily)
    days = sorted({d for c in cells for d in daily[c]})
    d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    step = timedelta(days=30 * a.months)

    def net(cell, lo, hi):
        return sum(v for d, v in daily[cell].items() if lo <= d < hi)

    print(f"{a.txt}: {len(cells)} ячеек, {days[0]}..{days[-1]}, окно {a.months} мес")
    print(f"{'окно отбора':>23} {'ячеек':>6} {'правило, ₽':>12} {'реестр, ₽':>12} {'на ячейку, ₽':>13}")
    rule_tot = base_tot = 0.0
    n_win = 0
    lo = d0
    while lo + 2 * step <= d1:
        mid, hi = lo + step, lo + 2 * step
        sel_lo, sel_hi, chk_hi = lo.isoformat(), mid.isoformat(), hi.isoformat()
        picked = [c for c in cells if net(c, sel_lo, sel_hi) > 0
                  and sum(fills[c][d] for d in fills[c] if sel_lo <= d < sel_hi) >= 50]
        rule = sum(net(c, sel_hi, chk_hi) for c in picked) * a.point
        base = sum(net(c, sel_hi, chk_hi) for c in cells) * a.point
        rule_tot += rule
        base_tot += base
        n_win += 1
        print(f"{sel_lo}..{sel_hi} {len(picked):6} {rule:12.0f} {base:12.0f} "
              f"{(rule / len(picked) if picked else 0):13.0f}")
        lo = mid
    print(f"ИТОГО за {n_win} окон: правило {rule_tot:+.0f} ₽, весь реестр {base_tot:+.0f} ₽")


if __name__ == "__main__":
    main()
