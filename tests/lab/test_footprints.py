"""Каркас footprints и образец A4 на синтетике (реальных данных здесь нет)."""
import json
import random
from collections import Counter
from datetime import date, datetime, timezone

from trader.lab.footprints import a4_bar_boundary as a4
from trader.lab.footprints import common

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())   # метка = МСК-стенка


def _bars(n_days=20, seed=1, boost_grid=None):
    """Случайное блуждание 10:00-18:44, объём логнормальный; boost_grid удваивает объём."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    for d in range(n_days):
        for m in range(600, 18 * 60 + 45):
            o = px
            px += rng.gauss(0, 20)
            v = rng.lognormvariate(3, 0.5)
            if boost_grid and m % boost_grid == 0:
                v *= 2
            rows.append([D0 + d * 86400 + m * 60, o, max(o, px) + 5, min(o, px) - 5, px, v])
    return rows


def _p(res, g, key="volume"):
    return next(r for r in res["rows"] if r["grid"] == g)[key]["p"]


def test_random_walk_no_effect():
    res = a4.analyze(_bars(), grids=(5, 15, 30, 60), draws=200, seed=0)
    for g in (5, 15, 30, 60):
        assert _p(res, g) > 0.05 and _p(res, g, "absmove") > 0.05, g


def test_control_catches_own_grid():
    res = a4.analyze(_bars(boost_grid=15), grids=(15, 7), draws=200, seed=0)
    assert _p(res, 15) < 0.01
    assert _p(res, 7) > 0.05
    row = res["rows"][0]["volume"]
    assert row["ci95"][0] > 1.5 and row["n_null"] == 200
    assert _p({"rows": res["halves"]["second"]}, 15) < 0.01


def test_exclusions_are_noted():
    """exclude_clearing по умолчанию False (в реальных данных RI клиринг
    торгуется) — явно включаем, чтобы проверить сам механизм исключения."""
    days, excl = a4.prepare(_bars(n_days=2), exclude_clearing=True)
    assert excl["клиринг"] == 2 * 5 and excl["открытие после перерыва"] == 2
    assert all(not (840 <= m < 845) for blk in days.values() for m, _, _ in blk)


def test_clearing_kept_by_default():
    days, excl = a4.prepare(_bars(n_days=2))
    assert "клиринг" not in excl
    assert any(840 <= m < 845 for blk in days.values() for m, _, _ in blk)


def test_prev_leak_fixed():
    """09.2026 баг: prev для «открытия после перерыва» двигался и по
    исключённым (вне сессии/клиринг) барам — бар 06:55 «спасал» 07:00 от
    исключения (разрыв всего 60 с). Фикс: prev только от оставленных баров,
    07:00 должно остаться исключённым даже при наличии бара 06:55."""
    rows = [[D0 + 415 * 60, 100, 105, 95, 100, 5],   # 06:55, до сессии
            [D0 + 420 * 60, 100, 105, 95, 101, 5],   # 07:00, открытие после перерыва
            [D0 + 421 * 60, 100, 105, 95, 101, 5]]   # 07:01, в сессии (08:00 раньше резалось)
    days, excl = a4.prepare(rows)
    assert [m for blk in days.values() for m, _, _ in blk] == [421]
    assert excl.get("до сессии") == 1
    assert excl.get("открытие после перерыва") == 1


def test_report_json_safe():
    res = a4.analyze(_bars(n_days=4), grids=(5,), draws=10)
    rep = common.report("A4", "RIZ6", ["2026-09-01", None], res["rows"], res["notes"],
                        n_days=res["n_days"], halves=res["halves"],
                        cost_pts=common.round_trip_cost_pts("RIZ6"), atr_min_pts=None)
    json.dumps(rep, allow_nan=False)
    assert set(rep) == {"id", "symbol", "window", "n_days", "rows", "halves",
                        "cost_pts", "atr_min_pts", "notes"}
    assert common.round_trip_cost_pts("SiZ6") is None


def test_by_day_across_midnight():
    t = D0 + 23 * 3600 + 50 * 60
    rows = [[t, 1], [t + 9 * 60, 1], [t + 11 * 60, 1]]
    days = common.by_day(rows)
    assert [len(days[date(2026, 9, 1)]), len(days[date(2026, 9, 2)])] == [2, 1]


def test_halves_whole_days():
    rows = _bars(n_days=5)
    a, b = common.halves(rows)
    assert len(a) + len(b) == len(rows)
    assert {common.day_of(r[0]) for r in a}.isdisjoint({common.day_of(r[0]) for r in b})
    assert max(r[0] for r in a) < min(r[0] for r in b)


def test_shuffle_within_day_keeps_multiset():
    vals = list(range(30))
    days = [i // 10 for i in range(30)]
    out = common.shuffle_within_day(vals, days, random.Random(3))
    assert out != vals
    for d in range(3):
        assert Counter(out[d * 10:d * 10 + 10]) == Counter(vals[d * 10:d * 10 + 10])


def test_pvalue_and_ci():
    r = common.pvalue_and_ci(2.0, [1.0] * 99, [1.9, 2.0, 2.1])
    assert r["p"] == 0.01 and r["n_null"] == 99 and r["ci95"][0] <= 2.0 <= r["ci95"][1]
    assert r["p_low"] == 1.0 and r["p_two"] == 0.02


def test_pvalue_two_sided_sees_opposite_sign():
    """Эффект обратного знака: верхний p ~1 («пусто»), p_low и p_two его видят."""
    null = [float(x) for x in range(-49, 50)]          # 99 нулей около 0
    r = common.pvalue_and_ci(-100.0, null, [])
    assert r["p"] == 1.0 and r["p_low"] == 0.01 and r["p_two"] == 0.02
    mid = common.pvalue_and_ci(0.0, null, [])
    assert mid["p_two"] == 1.0


def test_session_window():
    assert not common.in_session(6 * 60 + 59) and common.in_session(7 * 60)
    assert common.in_session(23 * 60 + 49) and not common.in_session(23 * 60 + 50)
    rows = [[D0 + m * 60, 1] for m in (419, 420, 1430)]
    kept, notes = common.session_rows(rows)
    assert [r[0] for r in kept] == [D0 + 420 * 60]
    assert notes == ["исключено до сессии: 1 баров", "исключено после сессии: 1 баров"]
