"""Ось «не выходить по перевороту» на четырёх инструментах и трёх семействах сигналов.

ЗАЧЕМ. На RI боевая спека lxk22 показала: снятие ВСЕХ флипов (позиция живёт до
тейка или стопа) улучшает итог парно, t=+2.40 по 17 контрактам, хотя сам уровень
остаётся нулём. Вопрос оператора 18.09.2026: держится ли это на BR, GD, Si и на
семействах 2EMA/3EMA, а не только у MACD на RI. Строка, которая живёт на одном
инструменте, — не ось.

ЧЕСТНОЕ СРАВНЕНИЕ. Конфиг у обоих вариантов ОДИН и голый: объём 1, без
усреднения, без мартингейла, без расписания сторон и фильтров в пунктах (они
подбирались под RI). Отличается ровно один ключ — flip_min_pts. Тейк 8×ATR и стоп
1% от входа заданы в процентах и ATR, поэтому переносятся между инструментами без
пересчёта; пороги в пунктах — нет, и потому выключены.

ЦЕНА. Итог в пунктах минус биржевая комиссия тейкера (taker_points не требует
стоимости пункта) минус полспреда на контракт, измеренное по архиву стакана
16.09.2026: RI 5.0, Si 1.0, GD 0.2, BR 0.05 пункта.

СУДЬЯ — парный тест по контрактам (разность «без флипов минус бой» на каждом
контракте), а не сумма: суммой правит один удачный квартал.

    PYTHONPATH=. python scripts/flip_block_matrix.py --dir <папка со склейками>
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics as st
import sys
import types
from concurrent.futures import ProcessPoolExecutor

from trader.lab.backtest import run_single_backtest
from trader.lab.commission import taker_points
from trader.lab.runtime import BacktestRuntime
from trader.lab.strategies.library import REGISTRY, make_on_bar

# полспреда в пунктах инструмента (медиана по архиву стакана, 16.09.2026)
SLIP = {"RI": 5.0, "Si": 1.0, "GD": 0.2, "BR": 0.05}
STRATEGIES = ("macd_shectory1", "shectory_2ema", "shectory_3ema")
FLIP_OFF = 20000.0          # порог заведомо больше любого хода = флип не исполняется

# Голая база: сигнал + тейк + стоп, больше ничего. Всё, что подбиралось под RI
# (расписание сторон, долина, разножка, усреднение, ставки), выключено.
BASE = {"qty": 1, "avg_max": 1, "avg_step_atr": 0, "k_avg": 0, "bet_step": 0,
        "avg_atr_n": 25, "tp_atr": 80, "sl_pct": 100, "sl_frac": 0,
        "min_gap_pts": 0, "min_gap_atr": 0, "cooldown_min": 0, "dv_bars": 0,
        "dv_range_pts": 0, "tod_m1": 0, "tod_m2": 0, "allow_long": 1,
        "allow_short": 1, "bar_offset_min": 0, "flatten_end": 1}


def _sig_params(strategy: str) -> dict:
    """Параметры сигнала из реестра — периоды EMA/MACD берём дефолтные, не подбираем."""
    d = REGISTRY[strategy]["default_params"]
    keys = {"macd_shectory1": ("fast", "slow", "signal"),
            "shectory_2ema": ("ema1", "ema2"),
            "shectory_3ema": ("ema1", "ema2", "ema3")}[strategy]
    return {k: d[k] for k in keys}


def _net_pts(bars, trades, symbol, slip: float) -> float:
    fills = [t for t in trades if t.get("time") is not None]
    cash = pos = cost = 0.0
    for f in sorted(fills, key=lambda t: t["time"]):
        s = 1 if f["side"] == "buy" else -1
        pos += s * f["qty"]
        cash -= s * f["qty"] * f["price"]
        cost += taker_points(symbol, f["price"], f["qty"]) + slip * f["qty"]
    return cash + pos * bars[-1].close - cost, len(fills)


def _run(item):
    base_sym, contract, bars = item
    slip = SLIP[base_sym]
    out = {}
    for strategy in STRATEGIES:
        for flip_off in (False, True):
            mod = types.ModuleType("m")
            mod.on_bar = make_on_bar(strategy)
            params = {**BASE, **_sig_params(strategy), "symbol": contract,
                      "flip_min_pts": FLIP_OFF if flip_off else 0}
            r = asyncio.run(run_single_backtest(mod, bars, contract, params,
                                                point_value=1.0,
                                                runtime_cls=BacktestRuntime))
            net, fills = _net_pts(bars, r["trades"], contract, slip)
            out[(strategy, flip_off)] = (net, fills)
    price = st.median([b.close for b in bars])
    return contract, price, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="папка со склейками RI.txt/BR.txt/…")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--inst", default="RI,Si,GD,BR")
    a = ap.parse_args()

    sys.path.insert(0, "scripts")
    from regime_step0 import load_ri

    rows = []
    for base_sym in a.inst.split(","):
        path = os.path.join(a.dir, f"{base_sym}.txt")
        by = {c: b for c, b in load_ri(path).items() if len(b) > 5000}
        print(f"\n### {base_sym}: контрактов {len(by)}, минуток "
              f"{sum(len(b) for b in by.values())}", flush=True)
        res: dict[tuple, dict[str, tuple]] = {}
        prices: dict[str, float] = {}
        with ProcessPoolExecutor(a.workers) as ex:
            for contract, price, out in ex.map(
                    _run, [(base_sym, c, b) for c, b in sorted(by.items())]):
                prices[contract] = price
                for key, val in out.items():
                    res.setdefault(key, {})[contract] = val
                print(f"  {contract} готов", flush=True)

        for strategy in STRATEGIES:
            on = res[(strategy, False)]
            off = res[(strategy, True)]
            contracts = sorted(on)
            d = [off[c][0] - on[c][0] for c in contracts]
            # Нормировка: пункты разных инструментов несравнимы, доля цены — да.
            dn = [100.0 * (off[c][0] - on[c][0]) / prices[c] for c in contracts]
            t = (st.mean(d) / (st.stdev(d) / len(d) ** 0.5)) if len(d) > 2 and st.stdev(d) else 0.0
            rows.append((base_sym, strategy, len(contracts),
                         sum(on[c][0] for c in contracts),
                         sum(off[c][0] for c in contracts),
                         sum(on[c][1] for c in contracts),
                         sum(off[c][1] for c in contracts),
                         sum(x > 0 for x in d), t, st.mean(dn)))
            print(f"  {strategy:16} бой {sum(on[c][0] for c in contracts):+10.0f} пт | "
                  f"без флипов {sum(off[c][0] for c in contracts):+10.0f} пт | "
                  f"парно {sum(x > 0 for x in d)}/{len(d)} t={t:+.2f}", flush=True)

    print(f"\n{'инстр':6} {'стратегия':16} {'конт':>5} {'бой пт':>11} "
          f"{'без флипов':>11} {'филлов бой':>11} {'без':>8} {'парно':>7} "
          f"{'t':>6} {'ср. разн, % цены':>17}")
    for r in rows:
        print(f"{r[0]:6} {r[1]:16} {r[2]:5d} {r[3]:+11.0f} {r[4]:+11.0f} "
              f"{r[5]:11d} {r[6]:8d} {r[7]:3d}/{r[2]:<3d} {r[8]:+6.2f} {r[9]:+17.3f}")


if __name__ == "__main__":
    main()
