"""
scale_in — «добор к средней и к зеркалу» (пререгистрация docs/scale-in-retest-2026.md).

После импульса (детектор retest.find_impulse) две лимитные покупки по qty_per_leg:
E1 = SMA(close, N) на закрытии прошлого бара (плывёт, пока не исполнена), E2 = E1 - X% (P-E1)
(фиксируется с исполнением E1). Тейк T = E1 + Y% (P-E1) на всю позицию, стоп = E2 - S пунктов.
Шорт зеркален. Лимитных заявок в движке нет: заявка, выставленная на закрытии бара t,
исполняется на барах t+1... по правилу «экстремум бара прошёл уровень на fill_pen шагов,
цена = уровень» через place_order_at.

ОТСТУПЛЕНИЯ (минимальное толкование):
  1. Пик P обновляется максимумом high каждого бара до исполнения E1, без повторной проверки
     imp_max. Уровни бара t+1 считаются по P и SMA на закрытии t (бар t+1 их не меняет).
  2. «E1 ниже close бара импульса»: на баре импульса нарушение снимает сетап; дальше то же
     условие (E1 ниже close прошлого бара) проверяется на каждом баре, при нарушении заявки
     в этот бар нет (сетап живёт).
  3. Уровни E1, E2, T привязаны к шагу цены в сторону «труднее исполнить» (E1/E2: вниз для
     лонга; T: вверх).
  4. На баре, где у стратегии был филл входа, тейк не проверяется (порядок high/low в баре
     неизвестен). Стоп-режим 0: close < уровня -> выход по open следующего бара.
  5. Рыночные выходы (стоп, время, конец дня) идут по цене с полуспредом half_spread_pts против;
     стоп по уровню (stop_mode=1) тоже, лимитные филлы без спреда. Выход на открытии бара t+1
     делается в on_bar бара t+1 по его open (place_order_at), это та же цена, что дал бы place_order.
  3b. hold_bars считается от бара исполнения E1.
  6. random_anchor=1: каждый настоящий импульс запускает вместо себя якорь в случайную минуту
     того же дня (RNG от seed+день+номер), P = экстремум imp_bars последних баров, ATR/уровни
     те же. Число якорей и стороны = числу и сторонам настоящих импульсов.
Позиция закрывается на баре >= 23:40 по его close, входы не после 23:00, сетап не переходит
через границу дня.
"""
import math
import random

from trader.lab.commission import is_weekend
from trader.lab.footprints.common import minute_of_day
from trader.lab.runtime import STLRuntime
from trader.lab.strategies.retest import _FLAT_MIN, _NO_ENTRY_MIN, _WK_FLAT_MIN, _WK_NO_ENTRY_MIN, find_impulse


def _snap(x: float, step: float, up: bool) -> float:
    n = x / step
    r = round(n)
    n = r if abs(n - r) < 1e-6 else (math.ceil(n) if up else math.floor(n))
    return round(n * step, 8)


def _levels(st: dict, bars, params: dict) -> None:
    """Уровни на закрытии последнего бара: st['e1'], st['e2'] (None = заявки в этот бар нет)."""
    n = max(1, int(params.get("N", 10)))
    step = float(params.get("price_step", 10))
    sgn = st["sgn"]
    st["e1"] = st["e2"] = None
    if len(bars) < n:
        return
    sma = sum(b.close for b in bars[-n:]) / n
    e1 = _snap(sma, step, up=sgn < 0)
    if sgn * (bars[-1].close - e1) <= 0:
        return
    dist = sgn * (st["P"] - e1)
    if dist <= 0:
        return
    st["e1"] = e1
    st["e2"] = _snap(e1 - sgn * float(params.get("X", 50)) / 100.0 * dist, step, up=sgn < 0)
    st["tp"] = _snap(e1 + sgn * float(params.get("Y", 100)) / 100.0 * dist, step, up=sgn > 0)


def _arm(sgn: int, P: float, atr: float, k: int, bars, params: dict):
    st = {"sgn": sgn, "P": P, "atr": atr, "k0": k, "qin": 0, "kf": None, "e1": None, "e2": None,
          "tp": None, "stop": None, "exit": ""}
    _levels(st, bars, params)
    return st if st["e1"] is not None else None       # на баре импульса E1 ниже close или сетап снят


def _random_anchor(bars, i: int, sgn: int, params: dict):
    imp_bars = max(1, int(params.get("imp_bars", 10)))
    atr_n = max(1, int(params.get("atr_n", 60)))
    w0 = i - imp_bars + 1
    if w0 < atr_n:
        return None
    atr = sum(bars[k].high - bars[k].low for k in range(w0 - atr_n, w0)) / atr_n
    if atr <= 0:
        return None
    P = max(b.high for b in bars[w0:i + 1]) if sgn > 0 else min(b.low for b in bars[w0:i + 1])
    return P, atr


async def on_start(stl: STLRuntime, params: dict) -> None:
    stl.log(f"scale_in started | symbol={params.get('symbol')} N={params.get('N')} X={params.get('X')} "
            f"S={params.get('S')} Y={params.get('Y')} random_anchor={params.get('random_anchor', 0)}")


def _inc(stl, key: str, n: int = 1) -> None:
    """Счётчики why_*/exit_* бэктест кладёт в extra результата (entry_reasons/exit_reasons)."""
    stl.set_state(key, int(stl.get_state(key, 0) or 0) + n)


def _fill(stl, ts, qty: int) -> None:
    """Контракты по филлам (why_fq) и с удвоением выходных (why_fqw): по ним сборщик пересчитывает
    брокерскую часть комиссии, когда движок считал RIH6 с ценой пункта 1.0."""
    _inc(stl, "why_fq", qty)
    _inc(stl, "why_fqw", qty * (2 if is_weekend(ts) else 1))


async def _market_exit(stl, symbol, st, price, ts, half, qty_leg, why):
    _inc(stl, "exit_" + why)
    _fill(stl, ts, st["qin"] * qty_leg)
    side = "sell" if st["sgn"] > 0 else "buy"
    await stl.place_order_at(symbol, side, st["qin"] * qty_leg, price - st["sgn"] * half, ts)


async def on_bar(stl: STLRuntime, params: dict) -> None:
    symbol = params["symbol"]
    atr_n = max(1, int(params.get("atr_n", 60)))
    imp_bars = max(1, int(params.get("imp_bars", 10)))
    n_sma = max(1, int(params.get("N", 10)))
    bars = await stl.get_bars(symbol, tf=1, n=max(atr_n + imp_bars + 1, n_sma + 1))
    if not bars:
        return
    i = len(bars) - 1
    cur = bars[i]
    k = int(stl.get_state("k", 0) or 0) + 1
    stl.set_state("k", k)
    minute = minute_of_day(cur.time)
    day = cur.time // 86400
    wk = is_weekend(cur.time)
    flat_min = _WK_FLAT_MIN if wk else _FLAT_MIN
    noent_min = _WK_NO_ENTRY_MIN if wk else _NO_ENTRY_MIN
    if stl.get_state("day") != day:
        old = stl.get_state("st")
        if old and old["qin"]:       # страховка: позиция не переходит через границу дня (короткая сессия)
            await _market_exit(stl, params["symbol"], old, cur.open, cur.time,
                               float(params.get("half_spread_pts", 5)), max(1, int(params.get("qty_per_leg", 1))), "gap")
        stl.set_state("day", day)
        stl.set_state("st", None)
        stl.set_state("rnd", None)
        stl.set_state("rn", 0)
    st = stl.get_state("st")
    leg = max(1, int(params.get("qty_per_leg", 1)))
    pen = float(params.get("fill_pen", 1)) * float(params.get("price_step", 10)) - 1e-9   # eps: плавающая точка
    half = float(params.get("half_spread_pts", 5))
    S = float(params.get("S", 100))
    stop_mode = int(params.get("stop_mode", 0))

    # ── отложенный рыночный выход: по open этого бара ──
    if st and st["qin"] and st["exit"]:
        await _market_exit(stl, symbol, st, cur.open, cur.time, half, leg, st["exit"])
        st = None

    if st:
        sgn = st["sgn"]
        worst, best = (cur.low, cur.high) if sgn > 0 else (cur.high, cur.low)
        side = "buy" if sgn > 0 else "sell"
        filled = False
        if st["qin"] == 0:                                   # ждём E1
            if minute < noent_min and st["e1"] is not None and sgn * (st["e1"] - worst) >= pen:
                _inc(stl, "why_e1")
                _fill(stl, cur.time, leg)
                await stl.place_order_at(symbol, side, leg, st["e1"], cur.time)
                st.update(qin=1, kf=k, e1p=st["e1"], stop=st["e2"] - sgn * S)
                filled = True
            elif k - st["k0"] >= int(params.get("wait_bars", 60)) or minute >= noent_min:
                st = None
            else:                                            # пик плывёт, уровни на следующий бар
                if sgn * (cur.high if sgn > 0 else cur.low) > sgn * st["P"]:
                    st["P"] = cur.high if sgn > 0 else cur.low
                _levels(st, bars, params)
        if st and st["qin"]:
            if st["qin"] == 1 and sgn * (st["e2"] - worst) >= pen:
                _inc(stl, "why_e2")
                _fill(stl, cur.time, leg)
                await stl.place_order_at(symbol, side, leg, st["e2"], cur.time)
                st["qin"] = 2
                filled = True
            exit_side = "sell" if sgn > 0 else "buy"
            if stop_mode == 1 and sgn * (worst - st["stop"]) <= 0:
                await _market_exit(stl, symbol, st, st["stop"], cur.time, half, leg, "stop")
                st = None
            elif not filled and sgn * (best - st["tp"]) >= pen:
                _inc(stl, "exit_tp_both" if st["qin"] == 2 else "exit_tp_one")
                _fill(stl, cur.time, st["qin"] * leg)
                await stl.place_order_at(symbol, exit_side, st["qin"] * leg, st["tp"], cur.time)
                st = None
            elif minute >= flat_min:
                await _market_exit(stl, symbol, st, cur.close, cur.time, half, leg, "eod")
                st = None
            elif (stop_mode == 0 and sgn * (cur.close - st["stop"]) < 0) \
                    or k - st["kf"] >= int(params.get("hold_bars", 240)):
                st["exit"] = "stop" if (stop_mode == 0 and sgn * (cur.close - st["stop"]) < 0) else "time"

    # ── поиск импульса / случайного якоря ──
    if st is None and minute < noent_min:
        allowed = [s for s in (1, -1) if int(params.get("allow_long" if s > 0 else "allow_short", 1))]
        if int(params.get("random_anchor", 0)):
            rnd = stl.get_state("rnd")
            if rnd is None:
                for s in allowed:
                    if find_impulse(bars, i, params, s):
                        cnt = int(stl.get_state("rn", 0) or 0)
                        stl.set_state("rn", cnt + 1)
                        if minute + 1 < noent_min:
                            r = random.Random(f"{params.get('seed', 0)}:{day}:{cnt}")
                            stl.set_state("rnd", {"min": r.randint(minute + 1, noent_min - 1), "sgn": s})
                        break
            elif minute >= rnd["min"]:
                stl.set_state("rnd", None)
                a = _random_anchor(bars, i, rnd["sgn"], params)
                if a:
                    st = _arm(rnd["sgn"], a[0], a[1], k, bars, params)
        else:
            for s in allowed:
                imp = find_impulse(bars, i, params, s)
                if imp:
                    st = _arm(s, imp["P"], imp["atr"], k, bars, params)
                    break
    stl.set_state("st", st)


async def on_stop(stl: STLRuntime, params: dict) -> None:
    stl.log("scale_in stopped")


def _p(key, label, default, lo, hi):
    return {"key": key, "label": label, "type": "number", "default": default, "min": lo, "max": hi}


STRATEGY_META = {
    "name": "scale_in - добор к средней и к зеркалу",
    "description": (
        "После импульса две лимитные покупки: от SMA(N) и на X% зеркала к пику; стоп S пунктов "
        "ниже второго входа; тейк на Y% первого пика. Шорт зеркально. Решения по закрытым барам."
    ),
    "source": "заказ оператора 01.10.2026, docs/scale-in-retest-2026.md",
    "params_schema": [
        {"key": "symbol", "label": "Инструмент", "type": "text", "default": "RIU6", "hint": "ТОЛЬКО контракт"},
        _p("atr_n", "ATR (баров)", 60, 10, 400),
        _p("imp_bars", "Импульс: окно (баров)", 10, 3, 60),
        _p("imp_min", "Импульс мин, x0.1 ATR", 30, 10, 100),
        _p("imp_max", "Импульс макс, x0.1 ATR", 100, 30, 300),
        _p("N", "SMA (минут)", 10, 1, 60),
        _p("X", "Зеркало E2, %", 50, 10, 100),
        _p("S", "Стоп ниже E2 (пунктов)", 100, 1, 2000),
        _p("Y", "Тейк, % к пику", 100, 10, 200),
        _p("wait_bars", "Ожидание E1 (баров)", 60, 5, 480),
        _p("hold_bars", "Держать не дольше (баров)", 240, 10, 960),
        _p("fill_pen", "Лимит: проход (шагов)", 1, 0, 10),
        _p("price_step", "Шаг цены", 10, 0.01, 100),
        _p("stop_mode", "Стоп: 0 close->open, 1 по уровню", 0, 0, 1),
        _p("half_spread_pts", "Полуспред рыночных выходов", 5, 0, 100),
        _p("allow_long", "Лонги (0/1)", 1, 0, 1),
        _p("allow_short", "Шорты (0/1)", 1, 0, 1),
        _p("qty_per_leg", "Контрактов на ногу", 1, 1, 10),
        _p("random_anchor", "Случайный якорь (0/1)", 0, 0, 1),
        _p("seed", "Сид якоря", 0, 0, 100000),
    ],
}
