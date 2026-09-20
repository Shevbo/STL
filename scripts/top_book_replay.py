"""Хит-парад лидерборда против архивного стакана: последние 37 дней RI.

ЗАПРОС ОПЕРАТОРА (20.09.2026): прогнать топ на дополнительных данных — стакан,
спред, глубина, — а не на барах.

ЧТО ЭТО МЕРЯЕТ. Строки лидерборда посчитаны исполнением по открытию следующего
бара: ни спреда, ни глубины, ни очереди. Архив сырого рынка (12.08-20.09.2026,
10 уровней стакана) позволяет исполнить ТЕ ЖЕ сделки по реальной встречной
стороне. Разница между двумя прогонами — цена исполнения, которую лидерборд не
видит. Окно чужое для всех строк: их подбирали по 20.07.2026 и раньше, так что
это заодно и выход за окно подгонки.

ТРИ ЦИФРЫ НА СТРОКУ: бар-исполнение (как в лидерборде), бар минус 5 пт
полспреда на контракт (грубая поправка), стакан (VWAP по уровням встречной
стороны). Последняя — ближайшее к правде, что у нас есть.

    PYTHONPATH=. python scripts/top_book_replay.py --rows top_ri.json \
        --bars RIU6.json --book book/ --code RIU6 --days 37
"""
from __future__ import annotations

import argparse
import asyncio
import json
import types
from concurrent.futures import ProcessPoolExecutor

from trader.lab.backtest import run_single_backtest
from trader.lab.book_replay import BookRuntime, load_dir
from trader.lab.commission import taker_points
from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

PV = 1.737744          # руб за пункт RI
SLIP_PTS = 5.0         # медиана полспреда RI по тому же архиву

_BOOK = None           # книга грузится один раз на процесс: 100 МБ архива


def _net(bars, trades, code, slip) -> tuple[float, int]:
    fills = sorted((t for t in trades if t.get("time") is not None),
                   key=lambda t: t["time"])
    cash = pos = cost = 0.0
    for f in fills:
        s = 1 if f["side"] == "buy" else -1
        pos += s * f["qty"]
        cash -= s * f["qty"] * f["price"]
        cost += taker_points(code, f["price"], f["qty"]) + slip * f["qty"]
    return (cash + pos * bars[-1].close - cost) * PV, len(fills)


def _init(book_dir: str, code: str) -> None:
    global _BOOK
    _BOOK = load_dir(book_dir, code)


def _one(item):
    idx, strategy, params, bars, code = item
    out = {}
    for mode in ("бар", "бар-спред", "стакан"):
        mod = types.ModuleType("m")
        mod.on_bar = make_on_bar(strategy)
        p = {**params, "symbol": code, "flatten_end": 1, "bar_offset_min": 0}
        kw = {"runtime_cls": BookRuntime,
              "runtime_kw": {"book": _BOOK, "max_gap_s": 60}} if mode == "стакан" else {}
        r = asyncio.run(run_single_backtest(mod, bars, code, p, point_value=PV, **kw))
        out[mode] = _net(bars, r["trades"], code,
                         SLIP_PTS if mode == "бар-спред" else 0.0)
    return idx, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="JSON со строками лидерборда")
    ap.add_argument("--bars", required=True, help="бары агента <код>.json")
    ap.add_argument("--book", required=True, help="папка архива книги")
    ap.add_argument("--code", default="RIU6")
    ap.add_argument("--days", type=int, default=37)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    rows = json.load(open(a.rows, encoding="utf-8"))
    raw = json.load(open(a.bars, encoding="utf-8"))["rows"]
    bars_all = [Bar(int(r[0]), *map(float, r[1:5]), int(r[5])) for r in raw]
    t_end = bars_all[-1].time
    t0 = t_end - a.days * 86400
    bars = [b for b in bars_all if b.time >= t0]
    print(f"{a.code}: баров в окне {len(bars)} ({a.days} дней), строк {len(rows)}")

    res: dict[int, dict] = {}
    with ProcessPoolExecutor(a.workers, initializer=_init,
                             initargs=(a.book, a.code)) as ex:
        tasks = [(i, r["strategy"], r["params"], bars, a.code)
                 for i, r in enumerate(rows)]
        for idx, out in ex.map(_one, tasks):
            res[idx] = out
            print(f"  строка {idx + 1} готова", flush=True)

    print(f"\n{'#':>3} {'стратегия':17} {'в окне подгонки':>16} {'бар':>11} "
          f"{'бар-спред':>11} {'СТАКАН':>11} {'филлов':>7}")
    tot = {"бар": 0.0, "бар-спред": 0.0, "стакан": 0.0}
    for i, r in enumerate(rows):
        o = res[i]
        for k in tot:
            tot[k] += o[k][0]
        print(f"{i + 1:3d} {r['strategy']:17} {r['net_profit']:16.0f} "
              f"{o['бар'][0]:+11.0f} {o['бар-спред'][0]:+11.0f} "
              f"{o['стакан'][0]:+11.0f} {o['стакан'][1]:7d}")
    n = len(rows)
    print(f"\nсредняя строка: бар {tot['бар'] / n:+.0f} руб, "
          f"бар-спред {tot['бар-спред'] / n:+.0f}, стакан {tot['стакан'] / n:+.0f}")
    pos = sum(res[i]["стакан"][0] > 0 for i in range(n))
    print(f"плюсовых по стакану: {pos} из {n}")


if __name__ == "__main__":
    main()
