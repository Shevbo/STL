"""retest: синтетика. ATR плоских баров = 1.0, пик 103.5, импульс 4 ATR, подтверждение на 102.4
(d = 1.1), вход по открытию бара p+3 (решение на закрытии p+2, wait_min=3), цена входа 102.3,
стоп 101.2, тейк (close >= пика) 103.5. Бар p = бар пика."""
import asyncio

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.retest import find_impulse, on_bar

SYM = "RIU6"
DAY0 = 1788307200                                   # 00:00
BASE = {"symbol": SYM, "wait_max": 120}


def _bars(start_min, rows, c0=100.0):
    """rows: (close, high, low); open = прошлый close, метка = старт + номер минуты."""
    out, prev = [], c0
    for n, (c, h, lo) in enumerate(rows):
        out.append(Bar(time=DAY0 + (start_min + n) * 60, open=prev, high=h, low=lo, close=c, volume=1))
        prev = c
    return out


def _flat(n, c=100.0):
    return [(c, c + 0.5, c - 0.5)] * n


IMPULSE = [(101.0, 101.3, 100.0), (102.0, 102.3, 101.0), (102.8, 103.0, 102.0), (103.2, 103.5, 102.8)]
CONFIRM = [(102.4, 103.0, 102.0)]                   # бар p+1
HOLD = [(102.3, 102.8, 101.8)]                      # бары ожидания и дрейфа
P = 70 + 3                                          # индекс бара пика (0-based в ряду)


def _series(tail, start_min=600, pre=70):
    return _bars(start_min, _flat(pre) + IMPULSE + CONFIRM + tail)


def _run(bars, **extra):
    async def go():
        rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
        p = {**BASE, **extra}
        while True:
            await on_bar(rt, p)
            if not rt.advance():
                break
        return [(o.side, round(float(o.price), 4), (o.fill_time - DAY0) // 60 - 600) for o in rt._orders]
    return asyncio.run(go())


def _mirror(bars):
    return [Bar(time=b.time, open=200 - b.open, high=200 - b.low, low=200 - b.high,
                close=200 - b.close, volume=b.volume) for b in bars]


RALLY = HOLD * 20 + [(103.6, 103.9, 102.5)] + [(103.6, 104.0, 103.0)] * 3     # возврат за 20 баров


def test_a_entry_after_confirm_exit_on_close_beyond_level_pnl_positive():
    o = _run(_series(HOLD + RALLY))
    assert o[0] == ("buy", 102.3, P + 3), o              # вход: открытие p+3 = close p+2
    # close 103.6 на баре p+2+1+20 ... выход на открытии следующего = его open (= close 103.6)
    assert o[1][0] == "sell" and o[1][1] == 103.6, o
    assert round(o[1][1] - o[0][1], 4) == 1.3 and len(o) == 2


def test_a2_mirror_down_impulse_gives_mirrored_trade():
    o = _run(_mirror(_series(HOLD + RALLY)))
    assert o[0] == ("sell", 97.7, P + 3) and o[1][0] == "buy" and o[1][1] == 96.4, o


def test_b_too_small_and_too_large_impulses_not_traded():
    small = [(100.5, 101.0, 100.0), (101.0, 101.5, 100.5), (101.5, 102.0, 101.0), (101.8, 102.0, 101.4)]
    big = [(103.0, 103.3, 100.0), (106.0, 106.3, 103.0), (109.0, 109.3, 106.0), (112.0, 112.5, 109.0)]
    for imp, conf in ((small, 100.8), (big, 110.5)):
        bars = _bars(600, _flat(70) + imp + [(conf, imp[-1][1] - 0.2, conf - 0.5)] + _flat(40, conf))
        assert _run(bars) == []


def test_c_stop_after_move_against_by_d():
    o = _run(_series(HOLD * 3 + [(101.0, 102.0, 100.8)] + [(100.5, 101.0, 100.0)] * 3))
    assert o[0][0] == "buy" and o[1][0] == "sell" and o[1][1] == 101.0, o   # close 101.0 < 101.2
    assert o[1][1] < o[0][1]


def test_d_time_exit_wait_max_from_peak():
    o = _run(_series(HOLD * 60), wait_max=30)
    assert o[0][2] == P + 3 and o[1] == ("sell", 102.3, P + 31), o


def test_e_invert_is_exact_mirror_of_trade():
    fwd, inv = _run(_series(HOLD + RALLY)), _run(_series(HOLD + RALLY), invert=1)
    assert inv[0] == ("sell", 102.3, P + 3) and inv[1][0] == "buy" and inv[1][2] == fwd[1][2]
    assert round((inv[0][1] - inv[1][1]) + (fwd[1][1] - fwd[0][1]), 4) == 0.0   # P&L зеркален: стоп на пике


def test_f_no_lookahead_future_does_not_change_decision():
    a = _run(_series(HOLD + [(103.6, 104.0, 102.5)] * 5))
    b = _run(_series(HOLD + [(90.0, 91.0, 80.0)] * 5))
    assert a[0] == b[0] == ("buy", 102.3, P + 3)


def test_g_not_earlier_than_wait_min():
    assert _run(_series(HOLD * 10 + RALLY), wait_min=6)[0] == ("buy", 102.3, P + 6)
    assert _run(_series(HOLD * 10 + RALLY), wait_min=3)[0][2] == P + 3


def test_h_flat_at_last_bar_of_day():
    rows = _flat(70) + IMPULSE + CONFIRM + HOLD * 190          # 20:00 + ... > 23:40
    o = _run(_bars(20 * 60, rows), wait_max=480)
    assert o[0][0] == "buy" and o[1][0] == "sell" and o[1][1] == 102.3, o
    assert (o[1][2] + 600 + DAY0 // 60) % 1440 == 23 * 60 + 40       # закрыто на баре 23:40, не наутро


def test_i_limit_mode_needs_penetration():
    bars = _series(HOLD * 3 + [(103.0, 103.6, 102.5), (103.0, 103.8, 102.5)] + HOLD * 4)
    o = _run(bars, tp_mode=1, price_step=0.1, fill_pen=2)
    assert o[1] == ("sell", 103.5, P + 6), o               # 103.6 уровень не прошёл на 0.2, 103.8 прошёл


def test_find_impulse_pure():
    bars = _bars(600, _flat(70) + IMPULSE)
    imp = find_impulse(bars, len(bars) - 1, {})
    assert imp["P"] == 103.5 and abs(imp["move"] / imp["atr"] - 4.0) < 1e-9
    assert find_impulse(bars, len(bars) - 2, {})["P"] == 103.0     # вторая половина: пик ещё не 103.5
