"""scale_in: синтетика. Плоские бары (c=100, ATR=1), импульс до пика 103.5 на баре 73.
N=2, X=50, Y=100, S=1.0, шаг 0.1, fill_pen=1 (проход 0.1), полуспред 0.2.
На закрытии 73: SMA2 = 103.0 -> E1 103.0; P-E1 = 0.5; E2 = 102.75 -> 102.7; T = 103.5; стоп 101.7.
Бар 74: low 102.8 <= 102.9 -> E1 исполнен по 103.0."""
import asyncio

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.scale_in import on_bar

SYM = "RIU6"
DAY0 = 1788307200
BASE = {"symbol": SYM, "N": 2, "X": 50, "Y": 100, "S": 1.0, "price_step": 0.1, "fill_pen": 1,
        "half_spread_pts": 0.2, "wait_bars": 60, "hold_bars": 240}
IMPULSE = [(101.0, 101.3, 100.0), (102.0, 102.3, 101.0), (102.8, 103.0, 102.0), (103.2, 103.5, 102.8)]
B74 = (102.9, 103.4, 102.8)                          # E1 по 103.0, E2 (102.7) не тронут


def _flat(n, c=100.0):
    return [(c, c + 0.5, c - 0.5)] * n


def _bars(rows, start_min=600):
    out, prev = [], 100.0
    for n, (c, h, lo) in enumerate(rows):
        out.append(Bar(time=DAY0 + (start_min + n) * 60, open=prev, high=h, low=lo, close=c, volume=1))
        prev = c
    return out


def _series(tail, start_min=600):
    return _bars(_flat(70) + IMPULSE + tail, start_min)


def _mirror(bars):
    return [Bar(time=b.time, open=200 - b.open, high=200 - b.low, low=200 - b.high,
                close=200 - b.close, volume=b.volume) for b in bars]


def _run(bars, **extra):
    async def go():
        rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
        p = {**BASE, **extra}
        while True:
            await on_bar(rt, p)
            if not rt.advance():
                break
        return [(o.side, round(float(o.fill_price), 4), o.qty, (o.fill_time - DAY0) // 60 - 600) for o in rt._orders]
    return asyncio.run(go())


def test_both_legs_and_tp_of_both():
    o = _run(_series([B74, (102.6, 102.9, 102.5), (103.0, 103.7, 102.9)] + _flat(3, 103.0)))
    assert o[:3] == [("buy", 103.0, 1, 74), ("buy", 102.7, 1, 75), ("sell", 103.5, 2, 76)], o


def test_only_first_leg_and_tp_of_one():
    o = _run(_series([B74, (103.0, 103.7, 102.9)] + _flat(3, 103.0)))
    assert o[:2] == [("buy", 103.0, 1, 74), ("sell", 103.5, 1, 75)], o


def test_tp_needs_penetration():
    o = _run(_series([B74, (103.0, 103.55, 102.9), (103.0, 103.6, 102.9)] + _flat(3, 103.0)))
    assert o[1] == ("sell", 103.5, 1, 76), o            # 103.55 не прошёл уровень на шаг


def test_stop_after_second_leg_on_close_below_e2_minus_s():
    o = _run(_series([B74, (102.6, 102.9, 102.5), (101.5, 102.6, 101.4), (101.4, 101.6, 101.3)] + _flat(3, 101.4)))
    assert o[:2] == [("buy", 103.0, 1, 74), ("buy", 102.7, 1, 75)]
    assert o[2] == ("sell", 101.3, 2, 77), o            # open бара 77 = close 101.5 минус полуспред 0.2


def test_fast_drop_through_e2_fills_second_leg_before_stop():
    o = _run(_series([B74, (101.0, 102.9, 100.9), (100.9, 101.2, 100.8)] + _flat(3, 100.9)))
    assert o[1] == ("buy", 102.7, 1, 75) and o[2] == ("sell", 100.8, 2, 76), o


def test_both_legs_in_entry_bar_then_stop():
    o = _run(_series([(101.0, 103.4, 100.5), (100.9, 101.2, 100.8)] + _flat(3, 100.9)))
    assert o[:2] == [("buy", 103.0, 1, 74), ("buy", 102.7, 1, 74)] and o[2][0] == "sell" and o[2][2] == 2, o


def test_stop_mode_1_fills_at_level():
    o = _run(_series([B74, (101.0, 102.9, 100.9)] + _flat(3, 100.9)), stop_mode=1)
    assert o[2] == ("sell", 101.5, 2, 75), o            # 101.7 - полуспред 0.2


def test_no_lookahead_future_does_not_change_past_decisions():
    a = _run(_series([B74] + [(103.6, 104.0, 102.5)] * 5))
    b = _run(_series([B74] + [(90.0, 91.0, 80.0)] * 5))
    assert a[0] == b[0] == ("buy", 103.0, 1, 74)


def test_unfilled_setup_expires_after_wait_bars():
    assert _run(_series([(103.5, 103.6, 103.3)] * 80)) == []


def test_flat_at_end_of_day_on_close():
    o = _run(_series([B74] + [(103.0, 103.3, 102.8)] * 190, start_min=20 * 60), hold_bars=9999)
    assert o[0][0] == "buy" and o[-1][0] == "sell" and o[-1][1] == 102.8, o
    assert (o[-1][3] + 600) % 1440 == 23 * 60 + 40            # закрыто на баре 23:40


def test_time_exit_hold_bars_at_next_open():
    o = _run(_series([B74] + [(103.0, 103.3, 102.8)] * 20), hold_bars=5)
    assert o[1] == ("sell", 102.8, 1, 80), o            # kf=74; k-kf=5 на баре 79; open бара 80 = 103.0 - 0.2


def test_short_is_mirror_of_long():
    o = _run(_mirror(_series([B74, (102.6, 102.9, 102.5), (103.0, 103.7, 102.9)] + _flat(3, 103.0))))
    assert o[:3] == [("sell", 97.0, 1, 74), ("sell", 97.3, 1, 75), ("buy", 96.5, 2, 76)], o


def test_allow_flags_block_sides():
    s = _series([B74, (102.6, 102.9, 102.5), (103.0, 103.7, 102.9)] + _flat(3, 103.0))
    assert _run(s, allow_long=0) == [] and _run(_mirror(s), allow_short=0) == []


def test_random_anchor_does_not_enter_on_impulse_and_flat_has_none():
    s = _series([(c, c + 0.5, c - 0.5) for c in [102.0, 103.0] * 370])
    a = _run(s, random_anchor=1, seed=3)
    assert a == _run(s, random_anchor=1, seed=3)        # детерминирован
    assert a and all(x[3] > 74 for x in a)              # якорь пришёл позже импульсного бара
    assert _run(s, random_anchor=1, seed=0) != a        # сид меняет момент
    assert _run(_bars(_flat(200)), random_anchor=1) == []   # нет импульса -> нет якоря
