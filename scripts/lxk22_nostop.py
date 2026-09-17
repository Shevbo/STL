"""Живой lxk22 за последние 36 дней: со стопом (как сейчас) и без стопа (17.09.2026).

Запрос оператора: в начале пути lxk22 зарабатывал без стопа, на перевороте сигнала.
Проверка на ТЕКУЩЕЙ боевой спеке (зеркало агента /api/v1/quik/robots-mirror), меняется
ОДИН параметр: sl_pct 100 (стоп 1% от входа) против 0. Всё остальное как в бою:
macd_shectory1 57/48/10, тейк 8×ATR(25), усреднение до 20 с k_avg 20, ставки 2/10,
эскалация 2/2, долина 60/300, расписание сторон 10:00/18:00 маска 3/2/1.

ПОТОЛОК ПОЗИЦИИ. Движок бэктеста max_position не знает, это кап агента. Воспроизведён
ровно как в robot_runner/runtime.py: заявка, которая УВЕЛИЧИВАЕТ позицию сверх потолка,
отбрасывается ЦЕЛИКОМ (не урезается); уменьшать можно всегда. Переворот −20 → +25
отбрасывается, робот остаётся в шорте.

ВРЕМЯ. Бары ISS — МСК-стенка как UTC, поэтому bar_offset_min = 0 (у раннера 180: его
бары в настоящем UTC).

ИСПОЛНЕНИЕ двумя способами: по open следующего бара и по архивному стакану RIU6
(BookRuntime, сверен с живыми филлами). P&L в рублях по MTM, пункт RIU6 = 1.737744 ₽
(эхо агента); комиссия — модель тейкера STL (на оборотистых днях завышает, см. память).

    PYTHONPATH=. python scripts/lxk22_nostop.py --bars RIU6.json --book book/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import types
from datetime import datetime, timezone

from trader.lab.backtest import run_single_backtest
from trader.lab.book_replay import BookRuntime, load_dir
from trader.lab.commission import commission_for
from trader.lab.runtime import BacktestRuntime, Bar, Order
from trader.lab.strategies.library import make_on_bar

PV = 1.737744
LIVE = {"qty": 1, "avg_max": 20, "fast": 57, "slow": 48, "signal": 10, "tp_atr": 80, "avg_atr_n": 25,
        "avg_step_atr": 21, "min_gap_pts": 0, "cooldown_min": 0, "cooldown_pct": 1, "nd_days": 5,
        "gap_auto": 0, "k_avg": 20, "sl_frac": 0, "sl_pct": 100, "allow_long": 1, "allow_short": 1,
        "dv_bars": 60, "dv_range_pts": 300, "bet_step": 2, "bet_max": 10, "super_y": 2, "super_z": 2,
        "tod_m1": 600, "tod_m2": 1080, "tod_s1": 3, "tod_s2": 2, "tod_s3": 1, "bar_offset_min": 0,
        "exit_only": False}
MAX_POSITION = 24


def capped(base):
    """Потолок позиции как у раннера: рост сверх потолка отбрасывается целиком."""
    class Capped(base):
        skipped = 0

        async def place_order(self, symbol, side, qty, price):
            pos = self._positions.get(symbol, {"side": "flat", "qty": 0})
            signed = pos["qty"] if pos["side"] == "long" else (-pos["qty"] if pos["side"] == "short" else 0)
            delta = qty if side == "buy" else -qty
            grows = abs(signed + delta) > abs(signed)
            if grows and abs(signed + delta) > MAX_POSITION:
                type(self).skipped += 1
                return Order(order_id="skipped-maxpos", symbol=symbol, side=side, qty=qty,
                             price=price, status="skipped")
            return await super().place_order(symbol, side, qty, price)
    return Capped


def evaluate(bars, trades, symbol):
    """Валовый MTM, комиссия, пик позиции, макс. просадка MTM — всё в рублях."""
    fills = sorted((t for t in trades if t.get("time") is not None), key=lambda t: t["time"])
    cash = pos = peak_pos = 0.0
    comm = 0.0
    eq_peak = dd = 0.0
    i = 0
    for b in bars:
        while i < len(fills) and fills[i]["time"] <= b.time:
            f = fills[i]
            s = 1 if f["side"] == "buy" else -1
            pos += s * f["qty"]
            cash -= s * f["qty"] * f["price"]
            comm += commission_for(symbol, f["price"], f["qty"], PV, taker=True)
            peak_pos = max(peak_pos, abs(pos))
            i += 1
        eq = (cash + pos * b.close) * PV - comm
        eq_peak = max(eq_peak, eq)
        dd = max(dd, eq_peak - eq)
    gross = (cash + pos * bars[-1].close) * PV
    return {"gross": gross, "comm": comm, "net": gross - comm, "fills": len(fills),
            "contracts": int(sum(f["qty"] for f in fills)), "peak_pos": int(peak_pos), "dd": dd,
            "end_pos": int(pos)}


def _contract_run(contract, bars, sl):
    """Один контракт склейки, один вариант стопа; по бару, с потолком позиции."""
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("macd_shectory1")
    params = {**LIVE, "sl_pct": sl, "symbol": contract, "flatten_end": 1}
    r = asyncio.run(run_single_backtest(mod, bars, contract, params, point_value=PV,
                                        runtime_cls=capped(BacktestRuntime)))
    return [(t["time"], t["side"], t["qty"], t["price"]) for t in r["trades"]]


def _pair(args):
    contract, bars = args
    return contract, bars, _contract_run(contract, bars, 100), _contract_run(contract, bars, 0)


def splice(ri_path: str, workers: int) -> None:
    """Вся история 2022-2026: где стоп менял сделки и в чью пользу.

    Разошедшаяся пара сделок с одной стороной и объёмом — это выход по стопу против
    выхода по сигналу. Стоп лучше (цена ушла дальше) или хуже (цена вернулась) считается
    по цене выхода. Прочие расхождения — путь после стопа поменялся (блок входа,
    эскалация), их вклад виден только в итоге по контракту.
    """
    from concurrent.futures import ProcessPoolExecutor
    from regime_step0 import load_ri
    by = load_ri(ri_path)
    helped = hurt = 0
    helped_pts = hurt_pts = 0.0
    print(f"{'контракт':9} {'сделок':>7} {'расход.':>7} {'со стопом пт':>13} {'без стопа пт':>13} {'стоп дал пт':>12} "
          f"{'просадка стоп пт':>17} {'без стопа пт':>13}")
    dd_stop = dd_free = []
    total = 0.0
    with ProcessPoolExecutor(workers) as ex:
        for contract, bars, a, b in ex.map(_pair, sorted(by.items())):
            def mtm(trades):
                cash = pos = 0.0
                for _, side, qty, price in trades:
                    sgn = 1 if side == "buy" else -1
                    pos += sgn * qty
                    cash -= sgn * qty * price
                return cash + pos * bars[-1].close
            na, nb = mtm(a), mtm(b)
            n_div = 0
            for x, y in zip(a, b):
                if x == y:
                    continue
                n_div += 1
                if x[1] == y[1] and x[2] == y[2]:
                    gain = (y[3] - x[3]) * x[2] if x[1] == "buy" else (x[3] - y[3]) * x[2]
                    if gain > 0:
                        helped += 1
                        helped_pts += gain
                    else:
                        hurt += 1
                        hurt_pts += gain
            total += na - nb
            # Страховка меряется просадкой, а не итогом: MTM по барам, в пунктах, без комиссии.
            def maxdd(trades):
                cash = pos = peak = dd = 0.0
                j = 0
                for bar in bars:
                    while j < len(trades) and trades[j][0] <= bar.time:
                        _, side, qty, price = trades[j]
                        sgn = 1 if side == "buy" else -1
                        pos += sgn * qty
                        cash -= sgn * qty * price
                        j += 1
                    eq = cash + pos * bar.close
                    peak = max(peak, eq)
                    dd = max(dd, peak - eq)
                return dd
            da, db = maxdd(sorted(a)), maxdd(sorted(b))
            dd_stop = dd_stop + [da]
            dd_free = dd_free + [db]
            print(f"{contract:9} {len(a):7} {n_div:7} {na:13.0f} {nb:13.0f} {na - nb:+12.0f} {da:17.0f} {db:13.0f}")
    print()
    print(f"стоп в плюс: {helped} выходов, {helped_pts:+.0f} пт; стоп в минус: {hurt} выходов, {hurt_pts:+.0f} пт")
    print(f"худшая просадка контракта: со стопом {max(dd_stop):.0f} пт, без стопа {max(dd_free):.0f} пт; "
          f"просадка без стопа глубже в {sum(f > t for t, f in zip(dd_stop, dd_free))} из {len(dd_stop)} контрактов")
    print(f"итог стопа за 2022-2026: {total:+.0f} пт (≈ {total * PV:+.0f} ₽ по сегодняшнему пункту; комиссия не учтена, "
          f"оборот вариантов почти одинаков)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars")
    ap.add_argument("--book")
    ap.add_argument("--start", default="2026-08-12")
    ap.add_argument("--end", default="2026-09-17")
    ap.add_argument("--ri", help="склейка RI.txt: вся история 2022-2026 вместо окна")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.ri:
        splice(a.ri, a.workers)
        return

    t0 = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp())
    rows = json.load(open(a.bars, encoding="utf-8"))["rows"]
    bars = [Bar(int(r[0]), *map(float, r[1:5]), int(r[5])) for r in rows if t0 <= r[0] < t1]
    book = load_dir(a.book, "RIU6")
    print(f"RIU6 {a.start}..{a.end}: баров M1 {len(bars)}, снимков стакана {len(book[0])}")
    print(f"{'вариант':26} {'исполн.':7} {'филлов':>6} {'контр.':>6} {'пик поз':>7} {'отброшено':>9} "
          f"{'валовый ₽':>11} {'комиссия ₽':>11} {'нетто ₽':>10} {'просадка ₽':>11} {'поз. в конце':>12}")
    for name, sl in (("живой: стоп 1% (sl_pct 100)", 100), ("БЕЗ стопа (sl_pct 0)", 0)):
        for ex, base, kw in (("бар", BacktestRuntime, {}), ("стакан", BookRuntime, {"book": book, "max_gap_s": 60})):
            cls = capped(base)
            cls.skipped = 0
            mod = types.ModuleType("m")
            mod.on_bar = make_on_bar("macd_shectory1")
            params = {**LIVE, "sl_pct": sl, "symbol": "RIU6", "flatten_end": 1}
            r = asyncio.run(run_single_backtest(mod, bars, "RIU6", params, point_value=PV,
                                                runtime_cls=cls, runtime_kw=kw))
            e = evaluate(bars, r["trades"], "RIU6")
            print(f"{name:26} {ex:7} {e['fills']:6} {e['contracts']:6} {e['peak_pos']:7} {cls.skipped:9} "
                  f"{e['gross']:11.0f} {e['comm']:11.0f} {e['net']:10.0f} {e['dd']:11.0f} {e['end_pos']:12}")


if __name__ == "__main__":
    main()
