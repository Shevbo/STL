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


# ── режим с фильтром ─────────────────────────────────────────────────────────────────────────────
import random  # noqa: E402


def _day(closes, start=420):
    """Бары от 07:00 по списку close; open = прошлый close, фитили +-1."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append([DAY0 + (start + i) * 60, prev, max(prev, c) + 1, min(prev, c) - 1, c, 1])
        prev = c
    return out


def _split(rows):
    body = [r for r in rows if 600 <= (r[0] % 86400) // 60 < 1420]
    return body, [r for r in rows if (r[0] % 86400) // 60 >= 1420]


PR = {**gs.DEFAULTS, **P, "stop_pts": 0}
CFG = {"on_th": 0.3, "off_th": 0.6, "dev_k": 99, "pos_k": 0, "cool": 15}


def test_signals_do_not_look_ahead():
    rnd = random.Random(3)
    px, cl = 1000, []
    for _ in range(900):
        px += rnd.gauss(0, 8)
        cl.append(round(px))
    rows = _day(cl)
    full = gs.signals(rows)
    for k in (200, 333, 500, 777):
        part = gs.signals(rows[:k + 1])
        for name in gs.SIGS:
            assert part[name][k] == full[name][k], (name, k)
    assert full["adx"][-1] is not None and full["er60"][400] is not None


def test_regime_removes_grid_on_trend_and_flattens():
    zig = [1000 + (15 if i % 2 else -15) for i in range(300)]          # 07:00-12:00 боковик
    trend = [1000 - 12 * j for j in range(1, 300)]                      # затем падение 12 пт/мин
    rows = _day(zig + trend)
    body, tail = _split(rows)
    sig = gs.signals(rows)
    ix = [k for k, r in enumerate(rows) if r in body]
    s = [sig["er30"][k] for k in ix]
    r = gs.simulate_regime(body, tail, PR, s, CFG)
    assert r["n_launch"] >= 1 and r["n_removals"] >= 1
    assert r["fills"][-1][4] == "regime" or r["fills"][-1][4] == "level"
    assert sum((1 if f[1] == "buy" else -1) * f[3] for f in r["fills"]) == 0     # позиция закрыта
    base = gs.simulate_day(rows, {**P, "stop_pts": 0})
    assert r["pnl_pts"] > base["pnl_pts"]                                       # без фильтра сетка тонет в тренде


def test_regime_on_sideways_matches_plain_grid():
    zig = [1000] * 181 + [1000 + (30 if i % 2 else -30) for i in range(700)]   # 10:00 бар плоский
    rows = _day(zig)
    rows[180] = [rows[180][0], 1000, 1000, 1000, 1000, 1]                 # бар 10:00 плоский: база 1000
    body, tail = _split(rows)
    sig = gs.signals(rows)
    ix = [k for k, r in enumerate(rows) if r in body]
    s = [sig["er30"][k] for k in ix]
    cfg = {**CFG, "on_th": 9, "off_th": 99}
    r = gs.simulate_regime(body, tail, PR, s, cfg)
    base = gs.simulate_day(rows, {**P, "stop_pts": 0})
    assert r["n_launch"] == 1 and r["n_removals"] == 0
    assert r["fills"] == base["fills"] and r["pnl_pts"] == base["pnl_pts"]


def test_random_control_keeps_time_share():
    rows = _day([1000 + (5 if i % 2 else -5) for i in range(900)])
    body, tail = _split(rows)
    durs = [40, 25, 60]
    cfg = {**CFG, "dev_k": 1e9}
    for seed in range(10):
        sch = gs._random_schedule(random.Random(seed), body, durs)
        r = gs.simulate_regime(body, tail, PR, [None] * len(body), cfg, schedule=sch)
        assert r["work"] == sum(d for s, d in sch) and len(sch) >= 2


# ── третья редакция: опоздание ───────────────────────────────────────────────────────────────────
def _dbody(rows):
    b = _bars(rows)
    return b, []


def test_delay_sideways_enters_late_at_close_minus_half_spread():
    # бар 1 пересёк уровень +1 (1100) и закрылся 1105: вход продажей по close - полспреда = 1100, тейкер
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1105, 1105, 1105)])
    r = gs.simulate_delay(body, tail, PR, z=1, k=1)
    assert [(f[1], f[2], f[4]) for f in r["fills"][:1]] == [("sell", 1100, "delay")]
    m, t = gs.costs_delay(r["fills"], "RIU6", gs.PV_RI)
    assert m == t and t > 0.45 * len(r["fills"])


def test_delay_impulse_moves_base_without_entry():
    # проход +1 и закрытие 1300 (дальше K=1 шага от 1100): входа нет, база перенесена, позиции нет
    body, tail = _dbody([FLAT, (1000, 1301, 1000, 1300), (1300, 1300, 1300, 1300)])
    r = gs.simulate_delay(body, tail, PR, z=1, k=1)
    assert r["fills"] == [] and r["stat"]["skip"] >= 1 and r["stat"]["entries"] == 0
    r2 = gs.simulate_delay(body, tail, PR, z=1, k=None)         # без переноса: входит
    assert r2["stat"]["entries"] >= 1


def test_delay_does_not_look_ahead():
    rnd = random.Random(11)
    px, rows = 1000, []
    for _ in range(300):
        o = px
        px = round(px + rnd.gauss(0, 35))
        rows.append((o, max(o, px) + 3, min(o, px) - 3, px))
    body, _t = _dbody([FLAT] + rows)
    full = gs.simulate_delay(body, [], PR, z=3, k=2)
    for m in (60, 140, 220):
        part = gs.simulate_delay(body[:m + 1], [], PR, z=3, k=2)
        ts_m = body[m][0]
        assert [f for f in part["fills"] if f[4] == "delay"] == [f for f in full["fills"] if f[4] == "delay" and f[0] <= ts_m]


def test_delay_random_skip_share_and_zero_one():
    rnd = random.Random(5)
    px, rows = 1000, []
    for _ in range(600):
        o = px
        px = round(px + rnd.gauss(0, 30))
        rows.append((o, max(o, px) + 2, min(o, px) - 2, px))
    body, _t = _dbody([FLAT] + rows)
    PRk = {**PR, "buys": 12, "sells": 12}
    lo = gs.simulate_delay(body, [], PRk, 1, None, skip_p=0.0, rng=random.Random(1))["stat"]
    hi = gs.simulate_delay(body, [], PRk, 1, None, skip_p=1.0, rng=random.Random(1))["stat"]
    mid = gs.simulate_delay(body, [], PRk, 1, None, skip_p=0.5, rng=random.Random(1))["stat"]
    assert lo["skip"] == 0 and hi["entries"] == 0 and hi["skip"] == hi["dec"] > 0
    assert 0.25 < mid["skip"] / mid["dec"] < 0.75


def test_delay_own_side_needs_next_bar_to_pass_tick():
    miss = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1105, 1090, 1095), (1095, 1095, 1095, 1095)])
    r = gs.simulate_delay(*miss, PR, z=1, k=None, own=True)
    assert r["stat"]["miss"] >= 1 and not [f for f in r["fills"] if f[4] == "own"]   # лимит 1110, next high 1105
    hit = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1111, 1090, 1095), (1095, 1095, 1095, 1095)])
    r = gs.simulate_delay(*hit, PR, z=1, k=None, own=True)
    assert r["fills"][0][1:5] == ("sell", 1110, 1, "own")                             # прошёл на тик: 1111
    m, t = gs.costs_delay(r["fills"], "RIU6", gs.PV_RI)
    assert m < t


# ── широкая многодневная сетка ───────────────────────────────────────────────────────────────────
def _wbars(days, ci=None):
    """days = [[(o, h, l, c), ...], ...]; день = DAY0 + 86400*i, бары с 10:00."""
    out = []
    for di, rows in enumerate(days):
        for k, (o, h, lo, c) in enumerate(rows):
            out.append([DAY0 + di * 86400 + (600 + k) * 60, o, h, lo, c, 1, (ci or [0] * len(days))[di]])
    return out


WSP = [{"key": "RIU6", "pv": 2.0, "margin": 1000.0}, {"key": "RIU6", "pv": 2.0, "margin": 1000.0}]
WP = {**gs.DEFAULTS, "step": 100, "buys": 3, "sells": 3, "stop_pts": 0, "tick": 1.0, "half": 0.0, "lot": 1, "fill_pen": 1}


def test_wide_position_lives_through_the_night():
    bars = _wbars([[(1000, 1000, 1000, 1000), (1000, 1101, 1000, 1105)], [(1105, 1105, 1105, 1105)] * 2])
    r = gs.simulate_wide(bars, {}, WSP, WP, 1000)
    assert r["pos"] == [-1, -1]
    assert r["last_pos"] == -1 and r["n_fills"] == 1 and r["realized"] == 0       # флэта нет, итог только MTM
    assert r["eq_g"][0] == r["eq_g"][1] == (1100 - 1105) * 2.0


def test_wide_roll_equity_continuous_and_fill_price_shifted():
    a = [[(1000, 1000, 1000, 1000), (1000, 1101, 1000, 1105)], [(1105, 1105, 1105, 1105)] * 2]
    b = [[(1205, 1205, 1205, 1205)] * 2, [(1205, 1301, 1205, 1250)] * 2]
    bars = _wbars(a + b, ci=[0, 0, 1, 1])
    r = gs.simulate_wide(bars, {4: 100.0}, WSP, WP, 1000)
    assert r["eq_g"][2] == r["eq_g"][1] == -10.0                     # перенос не меняет P&L до издержек
    # день 4: сдвинутый уровень 1300 (был 1200) исполняется продажей, а не 1200
    assert r["n_fills"] == 1 + 2 + 1 and r["pos"][3] == -2
    assert r["unit"][2] == r["unit"][1]                              # пассивный контроль не прыгает на смене


def test_wide_stop_closes_and_ends():
    bars = _wbars([[(1000, 1000, 1000, 1000), (1000, 1201, 1000, 1305), (1305, 1305, 1305, 1305)],
                   [(500, 500, 500, 500)] * 3])
    r = gs.simulate_wide(bars, {}, WSP, {**WP, "buys": 2, "sells": 2, "stop_pts": 100}, 1000)
    assert r["stop_ts"] is not None and r["last_pos"] == 0 and r["n_fills"] == 3          # 2 продажи + закрытие позиции 2
    assert r["max_pos"] == 2 and r["eq_g"][1] == r["eq_g"][0]                                  # после стопа сетка не работает


# ── четвёртая редакция: сброс по импульсу ────────────────────────────────────────────────────────
def test_reset_closes_position_at_next_open_and_moves_base():
    # бар 1: продажа +1 (1100), закрытие 1105; импульс зафиксирован на закрытии бара 1
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1105, 1105, 1105),
                         (1105, 1206, 1105, 1206), (1206, 1206, 1206, 1206)])
    imp = [False, True, False, False, False]
    r = gs.simulate_reset(body, tail, {**PR, "half": 2.0}, imp, cool=0)
    assert [(f[1], f[2], f[4]) for f in r["fills"][:2]] == [("sell", 1100, "level"), ("buy", 1107, "reset")]  # open бара 2 + 2
    assert r["resets"] == 1 and r["resets_pos"] == 1 and r["reset_pnl"] == 1100 - 1107
    # база перенесена к close бара 1 (1105): уровень +1 теперь 1205, бар 3 проходит 1206 и продаёт именно его
    assert r["fills"][2][1:3] == ("sell", 1205)


def test_reset_cool_pauses_grid():
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1105, 1105, 1105),
                         (1105, 1306, 1105, 1306), (1306, 1306, 1306, 1306)])
    imp = [False, True, False, False, False]
    r = gs.simulate_reset(body, tail, PR, imp, cool=15)
    assert [f[4] for f in r["fills"]] == ["level", "reset"]          # пауза 15 минут: проход 1205/1305 не торгуется


def test_reset_impulse_flags_do_not_look_ahead():
    rnd = random.Random(2)
    cl, px = [], 1000.0
    for i in range(420):                                              # 07:00-14:00 тихо, затем рывок и откат
        px += rnd.gauss(0, 1.0) if i < 300 else (6 if i < 308 else -3)
        cl.append(round(px))
    full = _day(cl)
    body = [r for r in full if 600 <= (r[0] % 86400) // 60 < 1420]
    flags = gs.impulse_flags(full, body, 60, 10)
    assert any(flags)
    for m in (250, 303, 307, 330):
        part = gs.impulse_flags(full[:m + 1], [r for r in body if r[0] <= full[m][0]], 60, 10)
        assert part == flags[:len(part)]


def test_reset_random_control_keeps_reset_count():
    rows = _day([1000 + (5 if i % 2 else -5) for i in range(900)])
    body, tail = _split(rows)
    sch = {10, 80, 200}
    r = gs.simulate_reset(body, tail, PR, [False] * len(body), cool=0, schedule=sch)
    assert r["resets"] == 3
