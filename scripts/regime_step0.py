"""Шаг 0 проекта «переключение стратегий по режимам» (14.09.2026): дневной P&L
стратегий реестра с параметрами ПО УМОЛЧАНИЮ на RI 2022-2026, поквартально по
контрактам. Из него проверяется, предсказуем ли результат следующего месяца по
прошлому. Нет связи — проект не нужен.

Бары — склейка RI.txt (Финам/TSLab, у каждой строки SECNAME = контракт), режется по
контрактам, поэтому швов внутри прогона нет. Время бара — МСК-стенка как UTC, как у ISS.
P&L в пунктах, валовый (без комиссии), по MTM на последнем баре дня; позиция
закрывается в конце контракта (flatten_end).

    PYTHONPATH=. python scripts/regime_step0.py --ri C:/Users/Boris/Downloads/RI.txt --out step0.csv
    PYTHONPATH=. python scripts/regime_step0.py ... --only macd_cross:RIH2   # замер скорости
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import time
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar
from trader.lab.strategies.library import REGISTRY, make_on_bar

# macd_shectory1 = сигнал macd_cross; bollinger_bo_m1 = принудительное усреднение.
SKIP = {"macd_shectory1", "bollinger_bo_m1"}
OVERRIDE = {"bet_step": 0}      # shectory_2ema по умолчанию с системой ставок


def load_ri(path: str) -> dict[str, list[Bar]]:
    by = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        next(f)
        for ln in f:
            t = ln.rstrip().split(",")
            ts = int(datetime.strptime(t[2] + t[3], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
            by[t[9]].append(Bar(ts, float(t[4]), float(t[5]), float(t[6]), float(t[7]), int(t[8])))
    return by


def aggregate(bars: list[Bar], tf: int) -> list[Bar]:
    """M1 -> M{tf}: бакеты по началу интервала. Движок tf игнорирует, отдаёт бары как есть.
    ponytail: прогрев стратегий в барах, pivot (2200) на H1 длиннее контракта и молчит."""
    if tf <= 1:
        return bars
    out: list[Bar] = []
    for b in bars:
        t = b.time - b.time % (tf * 60)
        if out and out[-1].time == t:
            o = out[-1]
            o.high, o.low, o.close, o.volume = max(o.high, b.high), min(o.low, b.low), b.close, o.volume + b.volume
        else:
            out.append(Bar(t, b.open, b.high, b.low, b.close, b.volume))
    return out


def daily_mtm(bars: list[Bar], fills: list[dict]) -> dict[str, tuple[float, int]]:
    """{дата: (P&L пунктов, филлов)}: equity = cash + pos*close на последнем баре дня."""
    fills = sorted((f for f in fills if f.get("time") is not None), key=lambda f: f["time"])
    cash = pos = last_close = 0.0
    i = n_day = 0
    prev_eq, out, day = 0.0, {}, None
    for b in bars:
        d = datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%d")
        if d != day and day is not None:
            eq = cash + pos * last_close
            out[day] = (eq - prev_eq, n_day)
            prev_eq, n_day = eq, 0
        day = d
        while i < len(fills) and fills[i]["time"] <= b.time:
            f = fills[i]
            s = 1 if f["side"] == "buy" else -1
            pos += s * f["qty"]
            cash -= s * f["qty"] * f["price"]
            n_day += f["qty"]           # контракты: переворот = 2
            i += 1
        last_close = b.close
    if day is not None:
        eq = cash + pos * last_close
        out[day] = (eq - prev_eq, n_day)
    return out


def _run(rid: str, contract: str, bars: list[Bar]) -> tuple[str, str, dict, float]:
    t0 = time.time()
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(rid)
    params = {**REGISTRY[rid]["default_params"], **OVERRIDE, "symbol": contract, "flatten_end": 1}
    r = asyncio.run(run_single_backtest(mod, bars, contract, params))
    return rid, contract, daily_mtm(bars, r["trades"]), time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ri", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="rid:контракт")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tf", type=int, default=1, help="минут в баре (15 = M15, 60 = H1)")
    a = ap.parse_args()

    by = {c: aggregate(b, a.tf) for c, b in load_ri(a.ri).items()}
    if a.only:
        rid, c = a.only.split(":")
        jobs = [(rid, c)]
    else:
        jobs = [(rid, c) for rid in REGISTRY if rid not in SKIP for c in by]
    print(f"контрактов {len(by)}, прогонов {len(jobs)}", flush=True)

    with open(a.out, "w", newline="", encoding="utf-8") as fo, ProcessPoolExecutor(a.workers) as ex:
        w = csv.writer(fo)
        w.writerow(["strategy", "contract", "date", "pnl_pts", "fills"])
        futs = [ex.submit(_run, rid, c, by[c]) for rid, c in jobs]
        for k, fu in enumerate(as_completed(futs), 1):
            rid, c, days, sec = fu.result()
            for d, (p, n) in sorted(days.items()):
                w.writerow([rid, c, d, round(p, 1), n])
            fo.flush()
            print(f"{k}/{len(jobs)} {rid} {c} {sum(p for p, _ in days.values()):.0f} пт {sec:.0f}с", flush=True)


if __name__ == "__main__":
    main()
