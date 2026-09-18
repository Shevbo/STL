"""Выход по стопу против выхода по перевороту — на боевой спеке lxk22, 2022-2026.

ГИПОТЕЗА ОПЕРАТОРА (18.09.2026): робот слишком часто выходит рано и с убытком,
потому что закрывается на кроссовере MACD. Что если выходить СТРОГО по стопу?

Новый параметр не нужен: `flip_min_pts` уже держит позицию, если цена отошла от
средней входа меньше порога. Порог заведомо больше любого хода = переворот не
исполняется никогда, выход остаётся только по тейку и стопу. Поэтому меряем ОСЬ
flip_min_pts от 0 (как в бою) до 20000 (выход только по стопу) при нескольких
стопах. Стоп обязателен: flip_min_pts армится только с ним (library.py), и
удержанный флип без стопа оставляет позицию без пола.

ЧЕСТНАЯ ЦЕНА. Итог считается по MTM в пунктах, минус комиссия тейкера STL и
минус проскальзывание 5 пт на филл (медиана полспреда RI по архиву стакана,
16.09.2026). Без этого вариант с меньшим числом сделок выигрывает просто потому,
что реже платит. Контроль тот же: смотреть не только итог, но и знак по
контрактам — строка, которая живёт на одном квартале, не кандидат.

Потолок позиции агента (24) воспроизведён как в robot_runner/runtime.py: рост
сверх потолка отбрасывается ЦЕЛИКОМ.

    PYTHONPATH=. python scripts/exit_rule_sweep.py RI.txt [--workers 8]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st
import types
from concurrent.futures import ProcessPoolExecutor

from trader.lab.backtest import run_single_backtest
from trader.lab.commission import commission_for
from trader.lab.runtime import BacktestRuntime, Order
from trader.lab.strategies.library import make_on_bar

PV = 1.737744          # руб за пункт RI (эхо агента, 17.09.2026)
SLIP_PTS = 5.0         # медиана полспреда RI по архиву стакана
MAX_POSITION = 24

LIVE = {"qty": 1, "avg_max": 20, "fast": 57, "slow": 48, "signal": 10, "tp_atr": 80,
        "avg_atr_n": 25, "avg_step_atr": 21, "min_gap_pts": 0, "cooldown_min": 0,
        "cooldown_pct": 1, "nd_days": 5, "gap_auto": 0, "k_avg": 20, "sl_frac": 0,
        "sl_pct": 100, "allow_long": 1, "allow_short": 1, "dv_bars": 60,
        "dv_range_pts": 300, "bet_step": 2, "bet_max": 10, "super_y": 2, "super_z": 2,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1,
        "bar_offset_min": 0, "exit_only": False}

# (имя, flip_min_pts, sl_pct, flip_hold_win). flip 20000 пт = 24% цены: переворот
# не исполнится никогда. hold_win: 1 = держать прибыльную, 2 = держать убыточную.
VARIANTS = [
    ("бой: флип вкл, стоп 1%", 0, 100, 0),
    ("только стоп 1%", 20000, 100, 0),
    ("держать прибыль, стоп 1%", 0, 100, 1),
    ("держать убыток, стоп 1%", 0, 100, 2),
    ("держать прибыль, стоп 2%", 0, 200, 1),
    ("держать прибыль, стоп 0.5%", 0, 50, 1),
]


def capped(base):
    """Потолок позиции как у раннера: рост сверх потолка отбрасывается целиком."""
    class Capped(base):
        async def place_order(self, symbol, side, qty, price):
            pos = self._positions.get(symbol, {"side": "flat", "qty": 0})
            signed = (pos["qty"] if pos["side"] == "long"
                      else -pos["qty"] if pos["side"] == "short" else 0)
            delta = qty if side == "buy" else -qty
            if abs(signed + delta) > abs(signed) and abs(signed + delta) > MAX_POSITION:
                return Order(order_id="skipped-maxpos", symbol=symbol, side=side,
                             qty=qty, price=price, status="skipped")
            return await super().place_order(symbol, side, qty, price)
    return Capped


def metrics(bars, trades, symbol) -> dict:
    """MTM в пунктах, комиссия и проскальзывание в пунктах, просадка MTM."""
    fills = sorted((t for t in trades if t.get("time") is not None), key=lambda t: t["time"])
    cash = pos = 0.0
    cost_pts = 0.0                      # комиссия + проскальзывание, в пунктах
    peak = dd = 0.0
    i = 0
    for b in bars:
        while i < len(fills) and fills[i]["time"] <= b.time:
            f = fills[i]
            s = 1 if f["side"] == "buy" else -1
            pos += s * f["qty"]
            cash -= s * f["qty"] * f["price"]
            cost_pts += commission_for(symbol, f["price"], f["qty"], PV, taker=True) / PV
            cost_pts += SLIP_PTS * f["qty"]
            i += 1
        eq = cash + pos * b.close - cost_pts
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    gross = cash + pos * bars[-1].close
    return {"gross": gross, "cost": cost_pts, "net": gross - cost_pts,
            "fills": len(fills), "contracts": int(sum(f["qty"] for f in fills)),
            "dd": dd}


def _run_contract(item):
    contract, bars = item
    out = {}
    for name, flip, sl, hw in VARIANTS:
        mod = types.ModuleType("m")
        mod.on_bar = make_on_bar("macd_shectory1")
        params = {**LIVE, "flip_min_pts": flip, "sl_pct": sl, "flip_hold_win": hw,
                  "symbol": contract, "flatten_end": 1}
        r = asyncio.run(run_single_backtest(mod, bars, contract, params,
                                            point_value=PV,
                                            runtime_cls=capped(BacktestRuntime)))
        out[name] = metrics(bars, r["trades"], contract)
    return contract, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("splice")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    import sys
    sys.path.insert(0, "scripts")
    from regime_step0 import load_ri
    by = {c: b for c, b in load_ri(a.splice).items() if len(b) > 5000}
    print(f"контрактов {len(by)}, минуток {sum(len(b) for b in by.values())}, "
          f"вариантов {len(VARIANTS)}")

    per: dict[str, dict[str, dict]] = {n: {} for n, *_ in VARIANTS}
    with ProcessPoolExecutor(a.workers) as ex:
        for contract, res in ex.map(_run_contract, sorted(by.items())):
            for name, m in res.items():
                per[name][contract] = m
            base = res[VARIANTS[0][0]]
            print(f"  {contract}: бой {base['net']:+8.0f} пт / "
                  f"{base['fills']:5d} филлов", flush=True)

    # Матрица контракт x вариант: строка, которая живёт на одном квартале, не кандидат.
    print(f"\n{'контракт':9} " + " ".join(f"{n[:11]:>11}" for n, *_ in VARIANTS))
    for contract in sorted(by):
        print(f"{contract:9} " + " ".join(
            f"{per[n][contract]['net']:+11.0f}" for n, *_ in VARIANTS))

    print(f"\n{'вариант':26} {'итог пт':>10} {'итог руб':>11} {'филлов':>7} "
          f"{'издержки пт':>12} {'вал пт':>9} {'+конт':>6} {'медиана конт':>13} "
          f"{'худш.просад':>12}")
    for name, *_ in VARIANTS:
        ms = per[name]
        net = sum(m["net"] for m in ms.values())
        print(f"{name:26} {net:+10.0f} {net * PV:+11.0f} "
              f"{sum(m['fills'] for m in ms.values()):7d} "
              f"{sum(m['cost'] for m in ms.values()):12.0f} "
              f"{sum(m['gross'] for m in ms.values()):+9.0f} "
              f"{sum(m['net'] > 0 for m in ms.values()):3d}/{len(ms):<2d} "
              f"{st.median([m['net'] for m in ms.values()]):+13.0f} "
              f"{max(m['dd'] for m in ms.values()):12.0f}")


if __name__ == "__main__":
    main()
