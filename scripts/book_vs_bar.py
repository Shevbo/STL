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
    ap.add_argument("--gap", type=int, default=60, help="порог свежести снимка, секунд")
    ap.add_argument("--all", action="store_true", help="весь реестр, а не шесть стратегий")
    a = ap.parse_args()

    rows = json.load(open(a.bars, encoding="utf-8"))["rows"]
    times, books = load_dir(a.book, a.code)
    lo, hi = times[0], times[-1]
    bars = aggregate([Bar(int(r[0]), *map(float, r[1:5]), int(r[5])) for r in rows if lo <= r[0] <= hi], a.tf)
    import bisect
    fresh = sum(1 for b in bars
                if (i := bisect.bisect_left(times, b.time)) < len(times) and times[i] - b.time <= a.gap)
    print(f"{a.code}: снимков стакана {len(times)}, баров M{a.tf} {len(bars)}, "
          f"со свежим стаканом (<= {a.gap} с) {fresh} = {fresh / max(len(bars), 1):.0%}")

    print(f"{'стратегия':15} {'филлов':>7} {'бар, пт':>10} {'стакан, пт':>11} {'разница':>10} "
          f"{'пт/филл':>8} {'спред ср':>8} {'медиана':>8} {'p90':>6} {'снос':>7} {'ночь%':>6} {'ночь дала':>10} {'нет кн':>6}")
    def mean_se(xs):
        """среднее и его ошибка: 'снос +7.3' без SE неотличим от одного ночного филла."""
        if not xs:
            return 0.0, 0.0
        m = sum(xs) / len(xs)
        if len(xs) < 3:
            return m, 0.0
        var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
        return m, (var / len(xs)) ** 0.5

    rows_out = []
    strats = [r for r in REGISTRY if r not in ("macd_shectory1", "bollinger_bo_m1", "williams_r")]         if a.all else STRATS
    for rid in strats:
        mod = types.ModuleType("m")
        mod.on_bar = make_on_bar(rid)
        params = {**REGISTRY[rid]["default_params"], "bet_step": 0, "symbol": a.code, "flatten_end": 1}
        r_bar = asyncio.run(run_single_backtest(mod, bars, a.code, params))
        r_bok = asyncio.run(run_single_backtest(mod, bars, a.code, params,
                                                runtime_cls=BookRuntime,
                                                runtime_kw={"book": (times, books), "max_gap_s": a.gap}))
        st = r_bok.get("fill_stats", {})
        n = len(r_bar["trades"])
        d = r_bok["net_profit"] - r_bar["net_profit"]
        k = max(st.get("book", 0), 1)
        sp = sorted(st.get("spread_each") or [0])
        hrs = st.get("hour_each") or []
        each = st.get("spread_each") or []
        is_night = [h < 9 or h >= 24 for h in hrs]
        night = sum(is_night) / max(len(hrs), 1)
        # Доля ВСЕЙ платы за спред, которую дают ночные и предоткрытые филлы: если она
        # много больше доли самих филлов, издержки лечатся расписанием, а не константой.
        tot = sum(each) or 1.0
        night_share = sum(v for v, nt in zip(each, is_night) if nt) / tot
        print(f"{rid:15} {n:7} {r_bar['net_profit']:10.0f} {r_bok['net_profit']:11.0f} {d:10.0f} "
              f"{(d / k):8.1f} {st.get('spread_pts', 0) / k:8.1f} {sp[len(sp) // 2]:8.1f} "
              f"{sp[min(len(sp) - 1, int(0.9 * len(sp)))]:6.0f} {st.get('drift_pts', 0) / k:7.1f} "
              f"{night:6.0%} {night_share:10.0%} {st.get('no_book', 0):6}")
        dr = st.get("drift_each") or []
        day_d = [v for v, nt in zip(dr, is_night) if not nt]
        night_d = [v for v, nt in zip(dr, is_night) if nt]
        rows_out.append((rid, mean_se(day_d), len(day_d), mean_se(night_d), len(night_d)))

    _print_drift(rows_out)


def _print_drift(rows_out):
    print("")
    print("снос отдельно день/ночь (пт на филл, +- ошибка среднего):")
    for rid, (dm, dse), dn, (nm, nse), nn in rows_out:
        print(f"  {rid:15} день {dm:+6.1f} +-{dse:4.1f} (n={dn:3})   ночь {nm:+7.1f} +-{nse:6.1f} (n={nn:3})")


if __name__ == "__main__":
    main()
