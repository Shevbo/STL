"""Райдер sl_rev (crazy stop): docs/crazy-stop-2026.md. Дефолт обязан давать сделки как до райдера."""
import json
import pathlib

from tests.lab.crazy_helpers import CASES, DAY0, SEEDS, run, walk
from trader.lab.runtime import Bar

GOLDEN = json.loads((pathlib.Path(__file__).parent / "data" / "crazy_golden.json").read_text())
BASE = dict(ema1=5, ema2=60, qty=1, sl_pct=50)       # sl 0.5% от средней: на 100 000 это 500 пт
REV = dict(BASE, sl_rev=1, rev_qty=1, rev_tp=1, rev_sl=1, rev_hold=240)


def _bars(closes, start_min=600):
    out, prev = [], closes[0]
    for n, c in enumerate(closes):
        out.append(Bar(time=DAY0 + (start_min + n) * 60, open=prev, high=max(prev, c) + 5, low=min(prev, c) - 5, close=c, volume=1))
        prev = c
    return out


def _scenario(after, start_min=600, n=300):
    """Долгий рост (лонг по 2EMA) -> скачок вниз на 1200 пт (стоп 0.5%) -> after."""
    up = [100000.0 + 10.0 * i for i in range(n)]
    jump = up[-1] - 1200.0
    return _bars(up + [jump] + after(jump), start_min)


def _orders(bars, **p):
    return run("shectory_2ema", bars, p)


def _minute(fill_time):
    return (fill_time - DAY0) // 60


def _net(o):
    return sum(q if s == "buy" else -q for s, q, _, _ in o)


# ── 1. дефолт = как до правки ──
def test_default_off_is_byte_identical_to_golden():
    stops = 0
    for name, (rid, p) in CASES.items():
        for s in SEEDS:
            bars = walk(s, day_gap=(s == 3))
            for extra in ({}, {"sl_rev": 0}, {"sl_rev": 0, "rev_qty": 2, "rev_tp": 3, "rev_mode": "rand", "seed": 5}):
                o, rt = run(rid, bars, {**p, **extra})
                assert [list(x) for x in o] == GOLDEN[f"{name}/{s}"], (name, s, extra)
                assert not any(k in rt._state for k in ("rev", "rev_cnt")), "райдер выключен: следов в состоянии быть не должно"
                stops += int(rt._state.get("exit_sl", 0))
    assert stops > 20, stops     # тест осмыслен: стопы в рядах реально срабатывают


# ── 2. райдер ──
def test_stop_reverses_position():
    bars = _scenario(lambda j: [j] * 20)
    on, rt = _orders(bars, **REV)
    i = next(k for k, o in enumerate(on) if o[0] == "sell")
    # на стопе: выход + обратная нога базового лота в один и тот же бар (оба по open следующего)
    assert on[i + 1][0] == "sell" and on[i][3] == on[i + 1][3] and on[i + 1][1] == 1, on
    assert rt._state.get("exit_sl") == 1


def test_reversed_take_profit_at_D_closed_bar_next_open():
    bars = _scenario(lambda j: [j - 15.0 * (i + 1) for i in range(60)])      # после скачка цена падает: шорт берёт тейк
    on, rt = _orders(bars, **dict(REV, rev_tp=1, rev_sl=2))
    sells = [o for o in on if o[0] == "sell"]
    buys = [o for o in on if o[0] == "buy"]
    ent = sells[1]                                   # обратная нога
    D = on[0][2] * 0.005                              # средняя входа лонга x 0.5%
    level = ent[2] - D
    k = next(i for i, b in enumerate(bars) if b.time > ent[3] and b.close <= level)   # первый закрытый бар за уровнем
    assert buys[-1][3] == bars[k + 1].time and buys[-1][2] == bars[k + 1].open, (buys, k)
    assert rt._state.get("exit_rtp") == 1


def test_reversed_stop_at_rev_sl_D():
    bars = _scenario(lambda j: [j + 20.0 * (i + 1) for i in range(60)])      # цена идёт против перевёрнутой шорт-ноги
    on, rt = _orders(bars, **dict(REV, rev_tp=3, rev_sl=0.5, rev_hold=900))
    assert rt._state.get("exit_rsl") == 1
    ent = [o for o in on if o[0] == "sell"][1]
    D = on[0][2] * 0.005
    k = next(i for i, b in enumerate(bars) if b.time > ent[3] and b.close >= ent[2] + 0.5 * D)
    last = [o for o in on if o[0] == "buy" and o[3] > ent[3]][0]
    assert last[3] == bars[k + 1].time


def test_reversed_time_exit_rev_hold():
    bars = _scenario(lambda j: [j] * 40)
    on, rt = _orders(bars, **dict(REV, rev_tp=5, rev_sl=5, rev_hold=7))
    assert rt._state.get("exit_rtime") == 1
    ent = [o for o in on if o[0] == "sell"][1]
    buy = [o for o in on if o[0] == "buy" and o[3] > ent[3]][0]
    assert _minute(buy[3]) - _minute(ent[3]) == 7, (ent, buy)


def test_base_signals_ignored_while_reversed_alive():
    # после скачка цена отскакивает вверх (база развернулась бы в лонг), перевёрнутый шорт держится: стоп далеко
    bars = _scenario(lambda j: [j + 12.0 * (i + 1) for i in range(30)])
    on, rt = _orders(bars, **dict(REV, rev_tp=5, rev_sl=5, rev_hold=20))
    ent = [o for o in on if o[0] == "sell"][1]
    between = [o for o in on if ent[3] < o[3] < ent[3] + 20 * 60]
    assert not between, between                     # ни одной заявки, пока перевёрнутая жива
    assert rt._state.get("exit_rtime") == 1


def test_modes_same_and_rand():
    bars = _scenario(lambda j: [j] * 20)
    same, _ = _orders(bars, **dict(REV, rev_mode="same", rev_hold=5))
    i = next(k for k, o in enumerate(same) if o[0] == "sell")
    assert same[i + 1][0] == "buy", same           # повторный вход в ту же сторону (лонг)
    sides = set()
    for seed in range(1, 12):
        r1, _ = _orders(bars, **dict(REV, rev_mode="rand", seed=seed, rev_hold=5))
        r2, _ = _orders(bars, **dict(REV, rev_mode="rand", seed=seed, rev_hold=5))
        assert r1 == r2                              # детерминирован
        j = next(k for k, o in enumerate(r1) if o[0] == "sell")
        sides.add(r1[j + 1][0])
    assert sides == {"buy", "sell"}


def test_rev_qty_one_is_base_lot_two_is_closed_volume():
    bars = _scenario(lambda j: [j] * 20)
    p = dict(REV, qty=2, avg_max=4, avg_step_atr=0, rev_hold=5)
    on1, _ = _orders(bars, **dict(p, rev_qty=1))
    on2, _ = _orders(bars, **dict(p, rev_qty=2))
    i = next(k for k, o in enumerate(on1) if o[0] == "sell")
    assert on1[i + 1][1] == 2                         # базовый лот qty=2
    j = next(k for k, o in enumerate(on2) if o[0] == "sell")
    assert on2[j + 1][1] == on2[j][1]                 # весь закрытый объём


# ── 3. перевёрнутая позиция не живёт через конец дня ──
# Сама база (2EMA всегда в рынке) флэта сессии не имеет и после выхода перевёрнутой входит заново по сигналу:
# проверяем, что именно перевёрнутая нога закрыта в срок, а не остаток базы.
def _rev_exit_minute(on):
    ent = [o for o in on if o[0] == "sell"][1]
    return _minute(next(o for o in on if o[0] == "buy" and o[3] > ent[3])[3])



def test_reversed_flat_at_session_end_weekday():
    n = 300
    bars = _scenario(lambda j: [j] * 40, start_min=23 * 60 + 20 - n, n=n)    # скачок в 23:20
    on, rt = _orders(bars, **dict(REV, rev_tp=9, rev_sl=9, rev_hold=900))
    assert rt._state.get("exit_reod") == 1 and rt._state.get("rev") is None, on
    assert _rev_exit_minute(on) % 1440 == 23 * 60 + 41


def test_reversed_flat_on_weekend_short_session():
    n = 300
    bars = _scenario(lambda j: [j] * 40, start_min=3 * 1440 + 18 * 60 + 20 - n, n=n)    # суббота, флэт с 18:50
    on, rt = _orders(bars, **dict(REV, rev_tp=9, rev_sl=9, rev_hold=900))
    assert rt._state.get("exit_reod") == 1 and rt._state.get("rev") is None, on
    assert _rev_exit_minute(on) % 1440 == 18 * 60 + 51


def test_reversed_closed_on_day_change_if_session_ended_early():
    n = 300
    day1 = _scenario(lambda j: [j] * 4, start_min=18 * 60 + 59 - (n + 4), n=n)   # будний день кончается 18:59 с живой перевёрнутой
    prev = day1[-1].close
    nxt = [Bar(time=DAY0 + (1440 + 600 + i) * 60, open=prev, high=prev + 5, low=prev - 5, close=prev, volume=1) for i in range(10)]
    on, rt = _orders(day1 + nxt, **dict(REV, rev_tp=9, rev_sl=9, rev_hold=900))
    assert rt._state.get("exit_reod") == 1 and rt._state.get("rev") is None, on
    assert _rev_exit_minute(on) == 1440 + 601


def test_count_fills_does_not_change_trades_and_counts_contracts():
    for name, (rid, p) in CASES.items():
        bars = walk(1)
        o, rt = run(rid, bars, {**p, "count_fills": 1})
        assert [list(x) for x in o] == GOLDEN[f"{name}/1"], name
        assert rt._state["why_fq"] == sum(x[1] for x in o), name
        assert rt._state["why_fqw"] >= rt._state["why_fq"]
