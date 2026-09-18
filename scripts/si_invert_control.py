"""Решающий контроль (fable, 18.09.2026): тот же выход с ИНВЕРТИРОВАННЫМ сигналом.
r_inv_every=1 переворачивает каждое решение. Если инверсия тоже в плюсе — плюс
делает механика выхода (тейк 0.2% против стопа 1%) и структура инструмента, а
не сигнал. Плюс разбивка по годам и без февраля-июня 2022 (планки и разрывы)."""
import sys
import asyncio
import types
sys.path.insert(0, "scripts")
from concurrent.futures import ProcessPoolExecutor
from flip_block_matrix import BASE, FLIP_OFF, _sig_params
from si_flip_controls import score
from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import BacktestRuntime
from trader.lab.strategies.library import make_on_bar
from regime_step0 import load_ri
D = "C:/Temp/claude/c--Dev-Shectory-Trade---Lab-AI-STL-Developers-backtests/7b144774-9734-4041-b42f-54080ae9402a/scratchpad"
STRATS = ("shectory_2ema", "shectory_3ema", "macd_shectory1")
SHOCK = {"SiH2", "SiM2"}          # февраль-июнь 2022: планки, остановки торгов


def one(item):
    contract, bars, strategy, inv = item
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar(strategy)
    p = {**BASE, **_sig_params(strategy), "symbol": contract,
         "flip_min_pts": FLIP_OFF, "r_inv_every": 1 if inv else 0}
    r = asyncio.run(run_single_backtest(mod, bars, contract, p, point_value=1.0,
                                        runtime_cls=BacktestRuntime))
    return contract, strategy, inv, score(bars, r["trades"], contract)


if __name__ == "__main__":
    by = {c: b for c, b in load_ri(f"{D}/Si.txt").items() if len(b) > 5000}
    tasks = [(c, b, s, inv) for c, b in sorted(by.items()) for s in STRATS
             for inv in (False, True)]
    res = {}
    with ProcessPoolExecutor(8) as ex:
        for contract, s, inv, sc in ex.map(one, tasks):
            res[(s, inv, contract)] = sc
    print(f"{'стратегия':16} {'сигнал':>10} {'нетто пт':>10} {'без 2022 шока':>14} "
          f"{'плюс.конт':>10} {'просадка':>9}")
    for s in STRATS:
        for inv in (False, True):
            v = {c: res[(s, inv, c)] for c in by}
            net = sum(x[0] for x in v.values())
            no_shock = sum(x[0] for c, x in v.items() if c not in SHOCK)
            print(f"{s:16} {'ИНВЕРС' if inv else 'как есть':>10} {net:+10.0f} "
                  f"{no_shock:+14.0f} {sum(x[0] > 0 for x in v.values()):7d}/{len(v):<3d} "
                  f"{max(x[1] for x in v.values()):9.0f}")
