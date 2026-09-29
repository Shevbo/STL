"""A4b на синтетике (реальных данных здесь нет, см. tests/lab/test_footprints.py).

Сиды 1..10 выбраны так, чтобы p случайного блуждания уверенно был > 0.05:
seed=3 проверен по всем трём статистикам части 1 (minute_class, ex_events) и
follow. Полная сессия 09:00-23:49 (обычные и выходные дни: FORTS торгует
выходные, здесь это специально нужно для проверки будни/выходные).
"""
import json
import random
from datetime import datetime, timezone

from trader.lab.footprints import a4b_boundary_followthrough as a4b
from trader.lab.footprints import common

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())  # понедельник
SESSION = range(9 * 60, 23 * 60 + 50)  # 09:00..23:49


def _bars(n_days=30, seed=1, boost_grid=None, boost_minutes=None, boost_mult=2.0,
          follow_pts=0.0, follow_h=3):
    """Случайное блуждание по полной сессии. boost_grid/boost_minutes удваивают
    (boost_mult) объём на указанных минутах; follow_pts/follow_h добавляют
    детерминированное продолжение хода на follow_h минут после границы."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    for d in range(n_days):
        extra = {}
        for m in SESSION:
            o = px
            px += rng.gauss(0, 20)
            is_boundary = (boost_grid is not None and m % boost_grid == 0) or \
                          (boost_minutes is not None and m in boost_minutes)
            v = rng.lognormvariate(3, 0.5)
            if is_boundary:
                v *= boost_mult
                if follow_pts:
                    px = o + 15.0  # гарантированно положительный ход границы
                    for k in range(1, follow_h + 1):
                        extra[m + k] = extra.get(m + k, 0.0) + follow_pts
            px += extra.pop(m, 0.0)
            c = px
            rows.append([D0 + d * 86400 + m * 60, o, max(o, c) + 5, min(o, c) - 5, c, v])
    return rows


def _rows(res, test, **filt):
    return [r for r in res["rows"] if r["test"] == test
            and all(r.get(k) == v for k, v in filt.items())]


def test_random_walk_no_effect():
    res = a4b.analyze(_bars(seed=3), grids=(60,), horizons=(3,), draws=120, seed=0)
    for r in _rows(res, "ex_events", group="будни", grid=60):
        assert r["null"]["p"] > 0.05, r
    for r in _rows(res, "minute_class", group="будни"):
        assert r["null"]["p"] > 0.05, r
    fol = _rows(res, "follow", grid=60, h=3, subgroup="all")[0]
    assert fol["null"]["p"] > 0.05, fol


def test_hourly_boundary_and_followthrough():
    """Объём удвоен на КАЖДОЙ границе часа (все часы), ход продолжается на
    +30 пт следующие 3 минуты — h:00 обязан выделиться, h:30/h:15 нет."""
    bars = _bars(seed=2, boost_grid=60, boost_mult=2.0, follow_pts=30.0, follow_h=3)
    res = a4b.analyze(bars, grids=(60,), horizons=(3,), draws=120, seed=0)

    h00 = _rows(res, "minute_class", group="будни", **{"class": "h:00"}, field="volume")[0]
    assert h00["null"]["p"] < 0.01
    assert 1.5 < h00["stat"] < 2.5

    h30 = _rows(res, "minute_class", group="будни", **{"class": "h:30"}, field="volume")[0]
    assert h30["null"]["p"] > 0.05  # только h:00 бустили, h:30 должен остаться шумом

    ex = _rows(res, "ex_events", group="будни", grid=60, field="volume")[0]
    assert ex["null"]["p"] < 0.01
    assert 1.5 < ex["stat"] < 2.5

    fol = _rows(res, "follow", grid=60, h=3, subgroup="all")[0]
    assert fol["null"]["p"] < 0.01
    assert fol["median"] > 0


def test_event_minutes_only_boost_dilutes():
    """Объём удвоен ТОЛЬКО в event_minutes (все пять лежат на сетке 30) —
    stat_full ловит примесь, ex_events после исключения возвращается к шуму."""
    bars = _bars(seed=3, boost_minutes=set(a4b.DEFAULT_EVENT_MINUTES), boost_mult=6.0)
    res = a4b.analyze(bars, grids=(30,), horizons=(3,), draws=100, seed=0)
    ex = _rows(res, "ex_events", group="будни", grid=30, field="volume")[0]
    assert ex["stat_full"] > 1.05          # примесь видна в полном расчёте
    assert ex["stat"] < ex["stat_full"]    # после исключения событий заметно ниже
    assert ex["null"]["p"] > 0.05          # и уже не значимо


def test_weekday_weekend_split():
    """FORTS торгует выходные — сплит по дню недели, не по факту торгов."""
    bars = _bars(n_days=9, seed=1)  # 09.2026 старт с понедельника, 9 дней -> сб+вс есть
    days, _ = a4b.prepare(bars)
    wd, we = a4b._split_weekend(days)
    assert wd and we
    assert all(d.weekday() < 5 for d in wd)
    assert all(d.weekday() >= 5 for d in we)
    assert set(wd) | set(we) == set(days)


def test_baseline_class_not_emitted():
    res = a4b.analyze(_bars(n_days=4, seed=1), grids=(60,), horizons=(3,), draws=10, seed=0)
    classes = {r["class"] for r in _rows(res, "minute_class")}
    assert "прочие" not in classes
    assert classes == {"h:00", "h:30", "h:15,h:45", "остальные кратные 5"}


def test_report_json_safe():
    bars = _bars(n_days=4, seed=1)
    res = a4b.analyze(bars, grids=(60,), horizons=(3,), draws=10, seed=0)
    rep = common.report("A4b", "RIZ6", ["2026-09-01", None], res["rows"], res["notes"],
                        n_days=res["n_days"], halves=res["halves"],
                        cost_pts=common.round_trip_cost_pts("RIZ6"), atr_min_pts=None)
    json.dumps(rep, allow_nan=False)
    assert set(rep) == {"id", "symbol", "window", "n_days", "rows", "halves",
                        "cost_pts", "atr_min_pts", "notes"}
    assert {r["test"] for r in rep["rows"]} == {"minute_class", "ex_events", "follow"}


def test_exclude_clearing_default_off():
    """По умолчанию клиринг не режется (RIZ6 торгует эти минуты)."""
    days, excl = a4b.prepare(_bars(n_days=2, seed=1))
    assert "клиринг" not in excl
    days2, excl2 = a4b.prepare(_bars(n_days=2, seed=1), exclude_clearing=True)
    assert excl2["клиринг"] == 2 * (5 + 20)  # 14:00-14:05 и 18:45-19:05
    assert sum(len(v) for v in days2.values()) < sum(len(v) for v in days.values())
