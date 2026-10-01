"""grid_sim: синтетика. База 1000, шаг 100, tick=1 (проход 1 пункт), полуспред 5.
Ожидаемые числа выведены руками, а не взяты из кода."""
from datetime import datetime, timezone

from trader.lab import grid_sim as gs

DAY0 = int(datetime(2026, 9, 2, tzinfo=timezone.utc).timestamp())
P = {"step": 100, "buys": 3, "sells": 3, "tick": 1.0, "half": 5.0}


def _bars(rows, start=600):
    return [[DAY0 + (start + i) * 60, o, h, lo, c, 1] for i, (o, h, lo, c) in enumerate(rows)]


def _sim(rows, **kw):
    return gs.simulate_day(_bars(rows), {**P, **kw})


FLAT = (1000, 1000, 1000, 1000)


def test_oscillation_pairs_buy_k_sell_k_plus_1():
    up = (1000, 1101, 1000, 1101)       # продажа уровня +1 (1100)
    dn = (1101, 1101, 999, 999)         # покупка уровня 0 (1000): база тоже уровень
    up2 = (999, 1101, 999, 1101)
    r = _sim([FLAT, up, dn, up2, (1101, 1101, 999, 999), up2, (1101, 1101, 999, 999)])
    sides = [(f[1], f[2]) for f in r["fills"]]
    assert sides == [("sell", 1100), ("buy", 1000)] * 3
    assert r["pnl_pts"] == 3 * 100      # три пары * шаг
    assert r["trades_pnl"] == [100, 100, 100] and r["max_pos"] == 1


def test_consumed_level_waits_for_neighbour_fill():
    r = _sim([FLAT, (1000, 1101, 1000, 1101), (1101, 1101, 1050, 1050),
              (1050, 1150, 1050, 1150), (1150, 1150, 1120, 1120), (1120, 1180, 1120, 1180)])
    # уровень +1 исполнен один раз; повторный проход 1101-1180 его не трогает, соседи (0, +2) не исполнены
    assert [(f[1], f[2]) for f in r["fills"][:1]] == [("sell", 1100)]
    assert len([f for f in r["fills"] if f[4] == "level"]) == 1


def test_neighbour_fill_returns_level():
    g = gs._Grid(1000, {**gs.DEFAULTS, **P})
    g.place(1050)
    side, px = g.fill(1)
    assert (side, px) == ("sell", 1100) and not gs.so_mod.grid_places_here(g.live, 1)
    g.place(1050)
    assert g.side[1] is None
    g.fill(2)                                   # филл соседа возвращает уровень 1
    assert gs.so_mod.grid_places_here(g.live, 1) and g.side[1] is None
    g.place(1150)                               # выставляется по рынку: ниже рынка покупка
    assert g.side[1] == "buy"


def test_orders_never_cross_market():
    g = gs._Grid(1000, {**gs.DEFAULTS, **P})
    assert g.side[0] is None                    # на цене рынка не выставлен
    assert {g.side[k] for k in (-3, -2, -1)} == {"buy"} and {g.side[k] for k in (1, 2, 3)} == {"sell"}
    g.place(1050)
    assert g.side[0] == "buy"
    g2 = gs._Grid(1000, {**gs.DEFAULTS, **P})
    g2.place(950)
    assert g2.side[0] == "sell"
    for k, s in g2.side.items():
        assert s is None or (s == "sell") == (g2.px[k] > 950)


def test_path_close_ge_open_goes_low_first():
    r = _sim([FLAT, (1000, 1101, 899, 1050)])
    assert [(f[1], f[2], f[4]) for f in r["fills"]] == [
        ("buy", 900, "level"), ("sell", 1000, "level"), ("sell", 1100, "level"), ("buy", 1050, "eod")]
    assert r["pnl_pts"] == 100 + 50


def test_path_close_lt_open_goes_high_first():
    r = _sim([FLAT, (1000, 1101, 899, 950)])
    assert [(f[1], f[2], f[4]) for f in r["fills"]] == [
        ("sell", 1100, "level"), ("buy", 1000, "level"), ("buy", 900, "level"), ("sell", 950, "eod")]
    assert r["pnl_pts"] == 100 + 50


TREND = [FLAT, (1000, 1000, 890, 890), (890, 890, 790, 790), (790, 790, 690, 690),
         (690, 690, 590, 590), (585, 585, 585, 585), (585, 1500, 585, 1500)]


def test_trend_down_to_stop_loss_and_grid_ends():
    r = _sim(TREND, stop_pts=100)               # стоп: 700 - 100 = 600; close 590 -> выход по open 585 - 5
    assert [f[2] for f in r["fills"]] == [900, 800, 700, 580]
    assert r["pnl_pts"] == (580 - 800) * 3      # три покупки, средняя 800
    assert r["n_stops"] == 1 and r["stop_time"] == DAY0 + (600 + 5) * 60
    assert r["max_pos"] == 3                    # после стопа сетка не работает, рост до 1500 не торгуется
    assert gs.so_mod.grid_stop_hit(gs._Grid(1000, {**gs.DEFAULTS, **P, "stop_pts": 100}).so, 590)


def test_stop_mode_touch_exits_at_stop_level():
    r = _sim(TREND, stop_pts=100, stop_mode=1)
    assert [f[2] for f in r["fills"]] == [900, 800, 700, 600]
    assert r["pnl_pts"] == (600 - 800) * 3


def test_restart_after_stop_starts_new_grid_same_day():
    r = _sim(TREND[:6] + [(585, 686, 585, 686)], stop_pts=100, restart="after_stop")
    assert r["n_stops"] == 1
    assert [(f[1], f[2]) for f in r["fills"][4:5]] == [("sell", 685)]   # новая сетка от open 585, уровень +1
    assert r["pnl_pts"] == (580 - 800) * 3 + (685 - 686)                # шорт закрыт по close 686 в 23:40


def test_no_start_bar_means_no_day():
    assert gs.simulate_day(_bars([FLAT], start=700), P) is None


def test_costs_two_bounds():
    r = _sim([FLAT, (1000, 1101, 899, 1050)])
    m, t = gs.costs(r["fills"], "RIU6", gs.PV_RI)
    assert abs(m - 0.45 * 4) < 1e-9 and t > m


def test_quiet_bar_skip_does_not_change_fills():
    import random
    rnd = random.Random(7)
    px, rows = 1000.0, []
    for _ in range(400):
        o = px
        px = round(px + rnd.gauss(0, 40))
        rows.append((o, max(o, px) + rnd.choice((0, 5)), min(o, px) - rnd.choice((0, 5)), px))
    for kw in ({}, {"stop_pts": 100}, {"stop_pts": 100, "stop_mode": 1}, {"stop_pts": 100, "restart": "after_stop"}):
        assert _sim(rows, **kw)["fills"] == _sim(rows, noskip=1, **kw)["fills"]
