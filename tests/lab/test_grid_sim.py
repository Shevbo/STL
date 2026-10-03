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


# ── часть 2б: короткая сетка после импульса ──────────────────────────────────────────────────────
def _sbody(rows):
    return _bars(rows), []


SP = {**gs.DEFAULTS, "tick": 1.0, "half": 0.0, "lot": 1, "fill_pen": 1}


def test_short_atr_does_not_look_ahead():
    rnd = random.Random(9)
    cl = [1000 + rnd.gauss(0, 5) for _ in range(700)]
    full = _day([round(c) for c in cl])
    body = [r for r in full if 600 <= (r[0] % 86400) // 60 < 1420]
    a = gs.atr10_of(full, body)
    full2 = full[:301] + [[r[0], 1, 999, 0, 1, 1] for r in full[301:]]
    body2 = [r for r in full2 if 600 <= (r[0] % 86400) // 60 < 1420]
    a2 = gs.atr10_of(full2, body2)
    k = next(i for i, r in enumerate(body) if r[0] == full[300][0])
    assert a[:k + 1] == a2[:k + 1] and a[0] is not None


def test_short_grid_removed_after_T_bars_and_flattens():
    # старт на баре 1 (close 1000), шаг 100 (atr 100, s=1), цена идёт вниз к 899: покупка 900, дальше тихо
    rows = [(1000, 1000, 1000, 1000), (1000, 1000, 1000, 1000), (1000, 1000, 899, 950)] + [(950, 950, 950, 950)] * 8
    body, tail = _sbody(rows)
    atr = [100.0] * len(body)
    r = gs.simulate_short(body, tail, SP, {1: 5.0, 3: 6.0}, atr, 1.0, 3, 4)       # T=4: жизнь баров 2..5; событие на баре 3 пропущено
    kinds = [f[4] for f in r["fills"]]
    assert r["grids"] == 1 and kinds == ["level", "life"]
    assert r["fills"][0][1:3] == ("buy", 900) and r["fills"][1][1] == "sell" and r["fills"][1][0] == body[6][0]   # open бара 6 (i=5 + 1)


def test_short_stop_beyond_one_step():
    rows = [(1000, 1000, 1000, 1000), (1000, 1000, 1000, 1000), (1000, 1000, 590, 600), (600, 600, 600, 600)] + [(600, 600, 600, 600)] * 6
    body, tail = _sbody(rows)
    r = gs.simulate_short(body, tail, SP, {1: 5.0}, [100.0] * len(body), 1.0, 2, 40)   # низ 800, стоп 700: close 600 <= 700
    assert r["stops"] == 1 and [f[4] for f in r["fills"]][-1] == "stop"


def test_short_control_matches_hour_and_volatility_decile():
    days = []
    for k in range(4):
        body = [[DAY0 + k * 86400 + (600 + i) * 60, 0, 0, 0, 0, 1] for i in range(120)]
        atr = [float(1 + (i * 7 + k * 13) % 50) for i in range(120)]
        days.append({"body": body, "atr": atr})
    edges, pm, ph = gs.short_pools(days)
    rng = random.Random(3)
    evs = {0: [30, 31, 32]}
    h0 = gs._hour(days[0], 30)
    for i in evs[0]:
        q0 = gs._decile(edges[gs._hour(days[0], i)], days[0]["atr"][i])
        for _ in range(20):
            sch = gs.draw_starts(days, edges, pm, ph, rng, {0: [i]}, True)
            (ck, d), = [(k_, list(v)[0]) for k_, v in sch.items()]
            assert gs._hour(days[ck], d) == gs._hour(days[0], i)
            assert gs._decile(edges[h0], days[ck]["atr"][d]) == q0 or gs._hour(days[ck], d) != h0


def test_short_reversion_stats():
    body, _ = _sbody([(1000, 1000, 1000, 1000), (1000, 1010, 990, 1010), (1010, 1010, 990, 990), (990, 1010, 990, 1010), (1010, 1010, 1010, 1010)])
    cross, eff = gs.reversion_stats(body, 0, 3)
    assert cross == 2 and abs(eff - 10 / (10 + 20 + 20)) < 1e-9
    assert gs.reversion_stats(body, 3, 3) is None


# ── ось tf ───────────────────────────────────────────────────────────────────────────────────────
def test_tf_aggregation_buckets_and_no_lookahead():
    from trader.lab.footprints.flex_range import aggregate_tf
    rnd = random.Random(4)
    full = _day([1000 + rnd.gauss(0, 4) for _ in range(120)])
    agg = aggregate_tf(full, 5)
    b0 = agg[2]
    seg = [r for r in full if b0[0] <= r[0] < b0[0] + 300]
    assert b0[1] == seg[0][1] and b0[2] == max(r[2] for r in seg) and b0[3] == min(r[3] for r in seg) and b0[4] == seg[-1][4]
    assert b0[6] == seg[-1][0] and b0[6] + 60 <= b0[0] + 300             # корзина закрыта к моменту ts_last + 60
    cut = [r for r in full if r[0] <= b0[6]]                             # данных после закрытия корзины нет
    assert aggregate_tf(cut, 5)[:3] == agg[:3]


def test_tf_atr_only_at_bucket_close_and_life_in_tf_time():
    full = _day([1000 + (i % 3) for i in range(400)])
    body = [r for r in full if 600 <= (r[0] % 86400) // 60 < 1420]
    days = [{"body": body, "full": full, "tail": []}]
    tfp = gs.tf_prepare(days, full, 5)
    a = days[0]["atr"]
    idx = [i for i, v in enumerate(a) if v]
    assert idx and all((body[i][0] // 60 + 1) % 5 == 0 for i in idx)      # только на последней минуте корзины
    rows = [(1000, 1000, 1000, 1000)] * 3 + [(1000, 1000, 899, 950)] + [(950, 950, 950, 950)] * 30
    b, t = _sbody(rows)
    r = gs.simulate_short(b, t, SP, {1: 5.0}, [100.0] * len(b), 1.0, 3, 2, 5)   # T=2 корзины x 5 минут = 10 минут жизни
    life = [f for f in r["fills"] if f[4] == "life"]
    assert r["grids"] == 1 and len(life) == 1 and life[0][0] == b[12][0]         # старт на баре 1: жизнь до ts+60+600 -> бар 11, выход по open бара 12
    assert tfp["agg"][0][6] <= tfp["agg"][1][0] + 299


# ── пятая редакция: триггерная радиация ──────────────────────────────────────────────────────────
TP = {**gs.DEFAULTS, **P, "stop_pts": 0, "tick": 1.0}
TRIG_ROWS = [FLAT, (1000, 1101, 1000, 1105), (1105, 1250, 1105, 1240), (1240, 1240, 1090, 1095)] + [(1095, 1095, 1095, 1095)] * 6


def test_trigger_no_entries_after_and_exit_only_at_average():
    body, tail = _dbody(TRIG_ROWS)
    r = gs.simulate_trigger(body, tail, TP, None, 1)                       # триггер: 1 исполненный уровень
    assert [(f[1], f[2], f[4]) for f in r["fills"]] == [("sell", 1100, "level"), ("buy", 1100, "be")]   # рост до 1250 новых входов не дал
    assert r["pnl_pts"] == 0 and r["kinds"]["be"] == 1 and r["trig_i"] == 1


def test_trigger_stop_and_session_flat_still_work():
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1105, 1105, 1105), (1105, 1305, 1105, 1305), (1305, 1305, 1305, 1305)])
    r = gs.simulate_trigger(body, tail, {**TP, "buys": 2, "sells": 2, "stop_pts": 100}, None, 1)
    assert r["kinds"]["stop"] == 1 and r["fills"][-1][4] == "stop" and r["pnl_pts"] < 0      # лимита на средней не дождались
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1105)] + [(1150, 1150, 1150, 1150)] * 6)
    r = gs.simulate_trigger(body, tail, TP, None, 1)
    assert r["kinds"]["eod"] == 1 and r["fills"][-1][4] == "eod" and r["eod_loss"] < 0


def test_trigger_in_profit_closes_at_next_open():
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1050), (1050, 1050, 1050, 1050), (1050, 1050, 1050, 1050)])
    r = gs.simulate_trigger(body, tail, TP, None, 1)
    assert [(f[1], f[2]) for f in r["fills"]] == [("sell", 1100), ("buy", 1055)] and r["pnl_pts"] == 45 and r["kinds"]["be"] == 1


def test_trigger_x_percent_and_forced_control():
    body, tail = _dbody([FLAT, (1000, 1030, 1000, 1030), (1030, 1030, 1030, 1030)] * 1 + [(1030, 1030, 1030, 1030)] * 3)
    assert gs.simulate_trigger(body, tail, TP, 2.0, None)["trig_i"] == 1               # 3% >= 2% на закрытии бара 1
    assert gs.simulate_trigger(body, tail, TP, 5.0, None)["trig_i"] is None
    assert gs.simulate_trigger(body, tail, TP, None, None, force_i=3)["trig_i"] == 3


def test_trigger_resume_flat_and_only_on_impulse_after_close():
    imp = [False, False, True, False, False, False, True, False, False, False]    # импульс на баре 2 раньше закрытия
    rows = TRIG_ROWS + [(1095, 1095, 1095, 1095)] * 0
    rows = rows[:4] + [(1095, 1095, 1095, 1095), (1095, 1095, 1095, 1095), (1095, 1296, 1095, 1296), (1296, 1296, 1296, 1296)] + [(1296, 1296, 1296, 1296)] * 2
    body, tail = _dbody(rows)
    r = gs.simulate_trigger(body, tail, TP, None, 1, resume="imp", imp=imp)
    assert r["resumes"] == 1                                                           # BE закрыт на баре 3; импульс на баре 2 не считается, на баре 6 да
    r2 = gs.simulate_trigger(body, tail, TP, None, 1, resume="flat")
    assert r2["resumes"] >= 1
    r0 = gs.simulate_trigger(body, tail, TP, None, 1)
    assert r0["resumes"] == 0


# ── шестая редакция: окна дня ────────────────────────────────────────────────────────────────────
def _fullday(spec_by_min):
    """Бары каждую минуту 07:00-23:50: цена из словаря {минута: (o,h,l,c)}, иначе плоско 1000."""
    out = []
    for m in range(420, 1430):
        o, h, lo, c = spec_by_min.get(m, (1000, 1000, 1000, 1000))
        out.append([DAY0 + m * 60, o, h, lo, c, 1])
    return out


WPAR = {**gs.DEFAULTS, **P, "stop_pts": 0, "tick": 1.0, "half": 0.0, "lot": 1, "fill_pen": 1}


def test_windows_cover_day_and_sliding_step():
    w = gs.sched_windows()
    assert (600, 1420) in w and (420, 600) in w and (1140, 1430) in w
    assert (420, 480) in w and (450, 510) in w and (420, 540) in w and (450, 570) in w
    assert max(b for _a, b in gs.D3_WINDOWS) == 1430 and sorted(gs.D5_WINDOWS)[0][0] == 420


def test_window_grid_only_inside_window_and_flat_at_window_end():
    spec = {}
    spec[650] = (1000, 1101, 1000, 1105)                    # внутри окна 10:00-12:00: продажа 1100
    spec[700] = (1105, 1105, 1105, 1105)
    spec[900] = (1105, 1400, 1105, 1400)                    # вне окна (15:00): сделок нет
    full = _fullday(spec)
    r = gs.window_sim(full, 600, 720, WPAR, None)
    assert [f[4] for f in r["fills"]] == ["level", "eod"] and r["fills"][-1][0] == full[720 - 420][0]    # закрыта в конце окна
    assert r["pnl_pts"] == 1100 - 1000                      # шорт 1100, флэт по close бара 12:00 (1105 -> до него цена 1105 держалась: close 1000 бара 720)


def test_window_trigger_variant_and_late_start_skipped():
    full = _fullday({650: (1000, 1101, 1000, 1105)})
    r = gs.window_sim(full, 600, 720, WPAR, (0.25, 1))
    assert r is not None and r["fills"] and r["fills"][0][4] == "level"
    assert gs.window_sim([x for x in full if not (600 <= (x[0] % 86400) // 60 < 620)], 600, 720, WPAR, None) is None


def test_window_metrics_and_random_windows_same_total_time_no_overlap():
    full = _fullday({610: (1000, 1010, 995, 1010), 620: (1010, 1010, 990, 990), 630: (990, 1010, 990, 1010)})
    m = gs.window_metrics(full, 600, 660, 5.0)
    assert m == (0.0, 4.0, 2, 60)                               # open = close окна, размах 20 при ATR 5, два пересечения open
    import random as _r
    pool = [(s, s + 60) for s in range(420, 1430 - 60 + 1, 30)]
    for seed in range(20):
        ws = gs.random_windows(_r.Random(seed), pool, 180)
        assert sum(b - a for a, b in ws) >= 180 and all(ws[i][1] <= ws[j][0] or ws[j][1] <= ws[i][0]
                                                         for i in range(len(ws)) for j in range(i + 1, len(ws)))


def test_trigger_k_mode_net_ignores_alternation_but_trend_triggers():
    alt = [FLAT, (1000, 1000, 899, 950), (950, 1001, 950, 1001), (1001, 1101, 1001, 1101), (1101, 1101, 999, 999)] + [(999, 999, 999, 999)] * 4
    body, tail = _dbody(alt)
    assert gs.simulate_trigger(body, tail, TP, None, 3, k_mode="fills")["trig_i"] is not None     # 3 исполненных уровня подряд
    assert gs.simulate_trigger(body, tail, TP, None, 3, k_mode="net")["trig_i"] is None           # позиция никогда не набрала 3 в одну сторону
    trend = [FLAT, (1000, 1000, 899, 905), (905, 905, 799, 805), (805, 910, 805, 900), (900, 900, 699, 705)] + [(705, 705, 705, 705)] * 3
    body, tail = _dbody(trend)
    assert gs.simulate_trigger(body, tail, TP, None, 3, k_mode="net")["trig_i"] == 4             # откат 805 -> 910 закрыл часть, потом набрали снова


def test_trigger_rearm_by_time_after_clean_exit_only():
    rows = TRIG_ROWS + [(1095, 1095, 1095, 1095)] * 30
    body, tail = _dbody(rows)
    r = gs.simulate_trigger(body, tail, TP, None, 1, resume="time", rearm_min=15)
    assert r["resumes"] == 1                                       # безубыток закрыт на баре 3, перевзвод не раньше чем через 15 минут
    r0 = gs.simulate_trigger(body, tail, TP, None, 1, resume="time", rearm_min=500)
    assert r0["resumes"] == 0                                       # окно ожидания длиннее дня
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1105), (1105, 1105, 1105, 1105), (1105, 1305, 1105, 1305)] + [(1305, 1305, 1305, 1305)] * 20)
    r2 = gs.simulate_trigger(body, tail, {**TP, "buys": 2, "sells": 2, "stop_pts": 100}, None, 1, resume="time", rearm_min=1)
    assert r2["kinds"]["stop"] == 1 and r2["resumes"] == 0         # после стопа боевая защита не перевзводится


# ── седьмая редакция: непрерывная сетка и самовзведение ───────────────────────────────────────────
def _nday(spec, di=0, n=60, start=420):
    """День di: n минутных баров с 07:00, цена 1000 плоско, spec {индекс бара: (o,h,l,c)}; бары без заданий продолжают close."""
    out, prev = [], 1000.0
    for k in range(n):
        o, h, lo, c = spec.get(k, (prev, prev, prev, prev))
        out.append([DAY0 + di * 86400 + (start + k) * 60, o, h, lo, c, 1])
        prev = c
    return out


NP = {**gs.DEFAULTS, **P, "stop_pts": 0, "tick": 1.0, "half": 0.0, "lot": 1, "fill_pen": 1}


def test_nextday_flat_at_end_and_no_exit_no_rearm():
    d0 = _nday({5: (1000, 1101, 1000, 1105)})
    d1 = _nday({}, di=1)
    r = gs.simulate_nextday([d0, d1], NP, None, None, 1, "next", 0, "first")
    assert r["st"]["pos"] == 0 and r["st"]["tk"][-1] == "end"            # позиция в конце окна закрыта
    assert r["rearms"] == 1 and r["overnight"] == 1                       # выхода не было: перевзвода нет, ночёвка с шортом


def test_nextday_rearm_only_next_day_after_start_plus_delay():
    d0 = _nday({15: (1000, 1101, 1000, 1105), 18: (1105, 1105, 1099, 1099), 30: (1099, 1250, 1099, 1250), 31: (1250, 1250, 999, 999)})
    d1 = _nday({2: (1000, 1000, 800, 800), 14: (800, 1101, 800, 1101)}, di=1)         # движение до start+10 мин (бар 10) и после
    r = gs.simulate_nextday([d0, d1], NP, None, 1, 1, "next", 10, "first")
    assert r["fires"] == 2 and r["rearms"] == 2                                       # K=1: защита дня 0, безубыток, перевзвод утром, защита нового набора
    fills = r["st"]["fills"]
    day1_start = d1[0][0]
    assert all(f[0] < d1[0][0] for f in fills if f[4] == "be")                         # выход в день 0
    after = [f for f in fills if f[0] >= day1_start]
    assert after and min(f[0] for f in after) >= day1_start + 10 * 60                  # раньше начало + D входов нет
    assert not [f for f in fills if d0[19][0] <= f[0] < day1_start]                     # внутри дня 0 перевзвода нет


def test_nextday_touches_two_separate_vs_consecutive_in_zone():
    mk = lambda spec: [_nday(spec), _nday({}, di=1)]                                   # noqa: E731
    sep = mk({15: (1000, 1201, 1000, 1150), 18: (1150, 1210, 1150, 1190)})              # два раздельных касания зоны 1200
    zone = mk({15: (1000, 1201, 1000, 1205), 18: (1205, 1210, 1205, 1205)})             # второй бар подряд в зоне: касания нет
    assert gs.simulate_nextday(sep, NP, None, None, 2, "next", 10, "first", trig_level=2)["fires"] == 1
    assert gs.simulate_nextday(zone, NP, None, None, 2, "next", 10, "first", trig_level=2)["fires"] == 0
    assert gs.simulate_nextday(zone, NP, None, None, 1, "next", 10, "first", trig_level=2)["fires"] == 1
    both = mk({15: (1000, 1201, 1000, 1150), 18: (1150, 1150, 799, 850)})                # вверх и вниз: стороны считаются отдельно
    assert gs.simulate_nextday(both, NP, None, None, 2, "next", 10, "first", trig_level=2)["fires"] == 0


def test_nextday_gap_fills_levels_at_level_price_and_overnight_counted():
    d0 = _nday({})
    d1 = _nday({0: (1250, 1250, 1250, 1250)}, di=1)                                      # открытие с гэпом через уровни 1100, 1200
    r = gs.simulate_nextday([d0, d1], {**NP, "buys": 3, "sells": 3}, None, None, 1, "next", 0, "first")
    sells = [f[2] for f in r["st"]["fills"] if f[4] == "level"]
    assert 1100 in sells and 1200 in sells and 1250 not in sells                        # по цене уровня, не по цене гэпа


# ── правка: рыночный выход на триггере = тейкер; перевзвод считается от дня фактического закрытия ──
def test_market_exit_at_trigger_is_taker_and_counted_as_breakeven():
    body, tail = _dbody([FLAT, (1000, 1101, 1000, 1050), (1050, 1050, 1050, 1050), (1050, 1050, 1050, 1050)])
    r = gs.simulate_trigger(body, tail, TP, None, 1)                       # в плюсе на триггере: закрытие по open с полспреда
    assert [f[4] for f in r["fills"]] == ["level", "bem"] and r["kinds"]["be"] == 1
    m, t = gs._fee_pair(r["fills"], "RIU6", gs.PV_RI)
    from trader.lab.commission import commission_for
    # level - мейкер (брокер), bem - тейкер: мейкер-граница дешевле тейкера ровно на комиссию уровня
    lvl = r["fills"][0]
    lvl_diff = commission_for("RIU6", lvl[2], 1, gs.PV_RI, taker=True, ts=lvl[0]) - commission_for("RIU6", lvl[2], 1, gs.PV_RI, taker=False, ts=lvl[0])
    assert abs((t - m) - lvl_diff) < 1e-9
    d0 = _nday({5: (1000, 1101, 1000, 1050)})
    rn = gs.simulate_nextday([d0, _nday({}, di=1)], NP, None, 1, 1, "next", 0, "first")
    assert "bem" in rn["st"]["tk"] or any(f[4] == "bem" for f in rn["st"]["fills"])


def test_nextday_rearm_day_is_day_after_decision_bar_stop_on_last_bar():
    # стоп на последнем баре дня 0, флэт на open дня 1: перевзвод в день 1, не раньше начало + D и строго после бара флэта
    d0 = _nday({5: (1000, 1101, 1000, 1105), 58: (1105, 1105, 1105, 1105), 59: (1105, 1400, 1105, 1400)})
    d1 = _nday({0: (1400, 1400, 1400, 1400), 3: (1400, 1400, 1400, 1400), 8: (1400, 1501, 1400, 1450)}, di=1)
    p = {**NP, "buys": 2, "sells": 2, "stop_pts": 100}
    r = gs.simulate_nextday([d0, d1], p, None, None, 1, "next", 3, "first")
    stop = [f for f in r["st"]["fills"] if f[4] == "stop"][0]
    assert stop[0] == d1[0][0]                                              # флэт на open первого бара дня 1
    assert r["rearms"] == 2                                                 # перевзвод в день 1 (дня 2 в окне нет)
    late = [f for f in r["st"]["fills"] if f[4] == "level" and f[0] > stop[0]]
    assert late and late[0][2] == 1400 + 100 and late[0][0] >= d1[3][0]     # база = open бара start+3 (1400), уровень +1 = 1500
    r0 = gs.simulate_nextday([d0, d1], p, None, None, 1, "next", 0, "first")
    assert r0["rearms"] == 2                                                # D=0: бар перевзвода всё равно строго после бара флэта


def test_nextday_rearm_after_breakeven_next_day_midday_goes_to_day_after():
    # шорт набран в день 0, защита K=1, цена остаётся выше средней всю ночь; безубыток исполняется днём дня 1: перевзвод день 2
    d0 = _nday({15: (1000, 1101, 1000, 1105)})
    d1 = _nday({0: (1105, 1105, 1105, 1105), 30: (1105, 1105, 1099, 1099)}, di=1)
    mk = lambda nd: [d0, d1] + [_nday({}, di=i) for i in range(2, nd)]            # noqa: E731
    assert gs.simulate_nextday(mk(2), NP, None, 1, 1, "next", 0, "first")["rearms"] == 1      # дня 2 нет: перевзвода нет
    r = gs.simulate_nextday(mk(3), NP, None, 1, 1, "next", 0, "first")
    be = [f for f in r["st"]["fills"] if f[4] == "be"][0]
    assert be[0] // 86400 == d1[0][0] // 86400 and r["rearms"] == 2                          # безубыток в день 1, перевзвод в день 2


# ── восьмая редакция: simulate_sweep ─────────────────────────────────────────────
def _b7(rows, roll_at=None, start=600):
    """Бары [ts,o,h,l,c,v,cidx]; roll_at = индекс первого бара второго контракта."""
    return [[DAY0 + (start + i) * 60, o, h, lo, c, 1, 1 if roll_at is not None and i >= roll_at else 0]
            for i, (o, h, lo, c) in enumerate(rows)]


def _sw(rows, rolls=None, roll_at=None, L=2, N=1, T=0.0, rearm="h1", naked=False, s=1, n=3, **kw):
    p = {**gs.DEFAULTS, **P, "buys": n, "sells": n, "stop_pts": s * 100, "lot": 1, "fill_pen": 1, "pvs": [1.0, 1.0]}
    return gs.simulate_sweep(_b7(rows, roll_at), rolls or {}, p, L, N, T, rearm, naked=naked, **kw)


def test_sweep_window_end_flat_and_no_cycle_without_levels():
    r = _sw([FLAT, (1000, 1101, 1000, 1101), (1101, 1101, 1050, 1050)], L=3, N=5)
    assert r["st"]["pos"] == 0 and r["ends"]["end"] == 1
    assert r["st"]["fills"][-1][4] == "end" and r["st"]["fills"][-1][2] == 1050   # close последнего бара


def test_sweep_rearm_not_before_term():
    drop = (1000, 1000, 500, 500)
    rows = [FLAT, drop] + [(500, 500, 500, 500)] * 100 + [(500, 650, 500, 650)] + [(650, 650, 650, 650)] * 5
    r = _sw(rows, rearm="h1", L=3, N=5, s=1)
    st = r["st"]
    stop = [f for f in st["fills"] if f[4] == "stop"]
    assert stop and r["ends"]["stop"] == 1
    later = [f for f in st["fills"] if f[0] > stop[0][0]]
    assert later and all(f[0] >= stop[0][0] + 3600 for f in later)               # новая сетка не раньше чем через час
    assert r["rearms"] == 2


def test_sweep_rearm_nd10_next_calendar_day():
    day = 24 * 60
    rows = [FLAT, (1000, 1000, 500, 500)] + [(500, 500, 500, 500)] * (day // 2 + 100)
    r = _sw(rows, rearm="nd10", L=3, N=5, s=1)
    assert r["ends"]["stop"] == 1 and r["rearms"] == 1                           # день + 1 06:10 за концом данных: нет баров
    rows = rows[:2] + [(500, 500, 500, 500)] * 2000
    r = _sw(rows, rearm="nd10", L=3, N=5, s=1)
    assert r["rearms"] == 2                                                      # день + 1 в данных есть (цикл взведён на баре)


def test_sweep_tp_counts_only_closed_pairs():
    up, dn = (1000, 1101, 1000, 1101), (1101, 1101, 999, 999)
    open_only = _sw([FLAT, up, (1101, 1101, 1090, 1090)], L=3, N=5, T=1)
    assert open_only["ends"]["tp"] == 0                                          # открытая нога без закрытой пары не считается
    r = _sw([FLAT, up, dn, (999, 999, 999, 999), (999, 999, 999, 999)], L=3, N=5, T=50)
    assert r["ends"]["tp"] == 1 and r["st"]["pos"] == 0
    assert [f[4] for f in r["st"]["fills"]].count("level") == 2
    r2 = _sw([FLAT, up, dn, (999, 999, 999, 999), (999, 999, 999, 999)], L=3, N=5, T=500)
    assert r2["ends"]["tp"] == 0                                                 # пара принесла 100 - 0.9, до 500 не дотянула


def test_sweep_bait_level_and_combos_never_beyond_n():
    r = _sw([FLAT, (1000, 1150, 1000, 1000), FLAT, FLAT], L=2, N=1, rearm="h3")
    assert r["ends"]["be"] == 0                                                  # до уровня 2 (1200) не дошли
    r = _sw([FLAT, (1000, 1201, 1000, 1201), (1201, 1201, 1100, 1100), (1100, 1100, 1100, 1100)], L=2, N=1, rearm="h3")
    assert r["ends"]["be"] == 1                                                  # шорт 1100/1200, выход лимитом по средней 1150
    assert all(v["L"] <= v["n"] for v in gs.sweep_combos(gs.COARSE)) and len(gs.sweep_combos(gs.COARSE)) == 60480


def test_sweep_roll_closes_position_and_restarts():
    rows = [FLAT, (1000, 1101, 1000, 1101), (1101, 1101, 1090, 1090), (2000, 2000, 2000, 2000), (2000, 2000, 2000, 2000)]
    r = _sw(rows, rolls={3: 1000.0}, roll_at=3, L=3, N=5)
    roll = [f for f in r["st"]["fills"] if f[4] == "roll"]
    assert len(roll) == 1 and roll[0][2] == 1090 and roll[0][0] == DAY0 + (600 + 2) * 60   # close последнего бара старого
    assert r["ends"]["roll"] == 1 and r["rearms"] == 2 and r["st"]["pos"] == 0


def test_sweep_step_is_share_of_weekly_range_and_week_median():
    # 5 будней (пн-пт), дневной размах 100, дни сдвинуты на 10 вверх: недельный размах окна из 5 дней = 100 + 4*10 = 140
    d0 = int(datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp())            # понедельник
    bars = []
    for k in range(10):
        t = d0 + k * 86400 + 600 * 60
        bars.append([t, 1000 + 10 * k, 1100 + 10 * k, 1000 + 10 * k, 1050, 1, 0])
    assert gs.week_range_median(bars) == 140
    # RI: метка 140 пт при недельном размахе 1400 = 10%; инструмент с размахом 700 (scale 0.5): шаг 70 пт, тик 10
    p = gs.sweep_vec_params({"step": 140, "n": 5, "s": 2}, 700 / 1400, 10.0, [1.0])
    assert p["step"] == 70 and p["stop_pts"] == 140
    p = gs.sweep_vec_params({"step": 50, "n": 5, "s": 1}, 0.001, 0.01, [1.0])
    assert abs(p["step"] - 0.05) < 1e-9
    p = gs.sweep_vec_params({"step": 50, "n": 5, "s": 1}, 0.0001, 1.0, [1.0])
    assert p["step"] == 1.0                                                      # минимум 1 тик


def test_sweep_durations_cycle_and_gap_in_days():
    drop = (1000, 1000, 500, 500)
    rows = [FLAT, drop] + [(500, 500, 500, 500)] * 100 + [(500, 650, 500, 650)] + [(650, 650, 650, 650)] * 5
    r = _sw(rows, rearm="h1", L=3, N=5, s=1)
    # цикл 1: бар 0 (600) -> флэт стопа на баре 2 (602): 2 мин; интервал до перевзвода через час после флэта: 60 мин
    assert r["durs"][0] == 2 * 60 and r["gaps"][0] == 3600
    ctx = gs.sweep_ctx(_b7(rows))
    s = gs.sweep_summary(r, ctx, ["X", "X"], [1.0, 1.0], daily=True)
    assert abs(s["gap_max"] - 3600 / 86400) < 1e-3 and s["dur_max"] >= s["dur_mean"] > 0
