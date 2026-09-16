"""Насколько врёт исполнение по бару: тот же прогон по архивному стакану (16.09.2026).

Одна стратегия, одни бары, одни метрики — меняется ТОЛЬКО исполнение:
  bar  — как сейчас: open следующего бара, спреда нет;
  book — первый снимок стакана не раньше того же момента, проход по уровням.

Разница = систематическая ошибка нынешних бэктестов на RIU6 (архив с 12.08.2026).

    PYTHONPATH=. python scripts/book_vs_bar.py --bars RIU6.json --book book/ --tf 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import types

from trader.lab.backtest import run_single_backtest
from trader.lab.book_replay import BookRuntime, load_dir
from trader.lab.runtime import Bar
from trader.lab.strategies.library import REGISTRY, make_on_bar

STRATS = ["keltner_bo", "bollinger_bo", "shectory_2ema", "macd_cross", "momentum", "stochastic"]


def aggregate(bars: list[Bar], tf: int) -> list[Bar]:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True, help="agent_bars/<contract>.json")
    ap.add_argument("--book", required=True, help="каталог с book-*.jsonl[.gz]")
    ap.add_argument("--code", default="RIU6")
    ap.add_argument("--tf", type=int, default=15)
    a = ap.parse_args()

    rows = json.load(open(a.bars, encoding="utf-8"))["rows"]
    times, books = load_dir(a.book, a.code)
    lo, hi = times[0], times[-1]
    bars = aggregate([Bar(int(r[0]), *map(float, r[1:5]), int(r[5])) for r in rows if lo <= r[0] <= hi], a.tf)
    print(f"{a.code}: снимков стакана {len(times)}, баров M{a.tf} {len(bars)} "
          f"({len(bars) and bars[0].time}..{len(bars) and bars[-1].time})")

    print(f"{'стратегия':15} {'филлов':>7} {'бар, пт':>10} {'стакан, пт':>11} {'разница':>10} "
          f"{'пт/филл':>8} {'глубины нет':>11} {'без стакана':>11}")
    for rid in STRATS:
        mod = types.ModuleType("m")
        mod.on_bar = make_on_bar(rid)
        params = {**REGISTRY[rid]["default_params"], "bet_step": 0, "symbol": a.code, "flatten_end": 1}
        r_bar = asyncio.run(run_single_backtest(mod, bars, a.code, params))
        r_bok = asyncio.run(run_single_backtest(mod, bars, a.code, params,
                                                runtime_cls=BookRuntime,
                                                runtime_kw={"book": (times, books)}))
        st = r_bok.get("fill_stats", {})
        n = len(r_bar["trades"])
        d = r_bok["net_profit"] - r_bar["net_profit"]
        print(f"{rid:15} {n:7} {r_bar['net_profit']:10.0f} {r_bok['net_profit']:11.0f} {d:10.0f} "
              f"{(d / n if n else 0):8.1f} {st.get('deep', 0):11} {st.get('no_book', 0):11}")


if __name__ == "__main__":
    main()
