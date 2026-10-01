"""R1 (ретест пика импульса) на синтетике. Реальных данных здесь нет (считает i9)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import common
from trader.lab.footprints import r1_retest as r1

D0 = int(datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp())   # 10:00, в сессии
P = {"draws": 200, "seed": 0}


def _bars(closes, day=0, hl=None):
    """Бары из рядов close, размах ровно 1 (ATR = 1); hl = {индекс: (high, low)} переопределение."""
    rows, prev = [], closes[0]
    for m, c in enumerate(closes):
        h, lo = (hl or {}).get(m, (max(prev, c) + 0.5, min(prev, c) - 0.5))
        rows.append([D0 + day * 86400 + m * 60, prev, h, lo, c, 10])
        prev = c
    return rows


def _flat(n, lvl=100.0, rng=None):
    return [lvl + (rng.uniform(-0.1, 0.1) if rng else 0) for _ in range(n)]


def _row(res, cut, bucket="all"):
    return next(r for r in res["rows"] if r["cut"] == cut and r["bucket"] == bucket)


def _cycle(rng, b=100.0, back=20):
    """Плоско 40, импульс +4.6 ATR за 3 бара, откат 1.5 ATR, возврат к пику через `back` баров."""
    c = _flat(40, b, rng)
    c += [b + 1.2, b + 2.4, b + 3.6]      # пик: close b+3.6, high b+4.1
    c += [b + 2.6, b + 2.1]               # подтверждение на первом (close <= P-1)
    step = 1.5 / (back - 4)
    c += [b + 2.1 + step * k for k in range(1, back - 3)]
    c += [b + 3.6] * 3
    return c


def test_random_walk_no_effect():
    rng = random.Random(11)
    rows, px = [], 100000.0
    for d in range(30):
        for m in range(300):
            px += rng.uniform(-2, 2)
            o = px - rng.uniform(-0.3, 0.3)
            rows.append([D0 + d * 86400 + m * 60, o, max(o, px) + 1, min(o, px) - 1, px, 10])
    res = r1.analyze(rows, {"atr_n": 30, "wait_max": 60}, **P)
    row = _row(res, "all")
    assert row["n"] >= 100, row["n"]
    assert abs(row["diff"]["stat"]) < 0.2, row["diff"]
    assert row["diff"]["p_two"] > 0.05, row["diff"]
    assert abs(row["outcome_mean"]) < 2.0, row["outcome_mean"]


def test_inserted_retests():
    rng = random.Random(3)
    rows = []
    for d in range(8):
        c = []
        for k in range(4):
            c += _cycle(rng, 100.0 + 3.6 * k)
        rows += _bars(c, day=d)
    res = r1.analyze(rows, {"atr_n": 30, "wait_max": 60}, **P)
    row = _row(res, "all")
    assert row["n"] >= 25, row["n"]
    assert row["p_retest"] > 0.8, row
    assert row["diff"]["stat"] > 0.7 and row["diff"]["p_two"] < 0.05, row["diff"]
    assert 15 <= row["bars_to_retest_median"] <= 22, row["bars_to_retest_median"]


def test_no_lookahead_in_detector():
    rng = random.Random(5)
    c = _flat(40, 100.0, rng) + [101.2, 102.4, 103.6, 102.6, 102.1]
    ev = r1.find_impulses(_bars(c + _flat(30, 100.0)), {"atr_n": 30})
    ev2 = r1.find_impulses(_bars(c + [150.0 + 7 * k for k in range(30)]), {"atr_n": 30})
    ev3 = r1.find_impulses(_bars(c + [-50.0] * 30), {"atr_n": 30})
    first = next(e for e in ev if e["side"] == 1)
    assert first["i_conf"] == 43 and first["t_p"] == 42
    for other in (ev2, ev3):
        assert [e for e in other if e["i_conf"] <= 43 and e["side"] == 1] == [first]


def _scenario(wait_min):
    c = _flat(40) + [101.2, 102.4, 103.6, 102.6]          # подтверждение на баре 43 = t_p+1
    c += [103.0, 100.6] + [100.6] * 6                      # бар 44: касание P раньше t_p+3; затем падение к M
    rows = _bars(c, hl={44: (104.2, 102.6)})
    p = {"atr_n": 30, "wait_min": wait_min, "wait_max": 30}
    ev = next(e for e in r1.find_impulses(rows, p) if e["side"] == 1)
    return r1.evaluate(rows, ev, p)


def test_early_retest_not_counted():
    assert _scenario(3)["kind"] == "mirror"
    r = _scenario(1)
    assert r["kind"] == "retest" and r["bars_to_retest"] == 2


def test_too_small_or_too_big_impulse_no_events():
    rng = random.Random(9)
    for step, nb in ((0.5, 3), (3.0, 4)):       # ~2 ATR и ~12 ATR
        c = _flat(40, 100.0, rng) + [100.0 + step * (k + 1) for k in range(nb)]
        c += [c[-1] - 1.5, c[-1] - 2.0] + [c[-1] - 2.0] * 20
        assert not [e for e in r1.find_impulses(_bars(c), {"atr_n": 30}) if e["side"] == 1]


def test_down_side_mirrors_up():
    rng = random.Random(3)
    up = _bars(_cycle(rng) + _flat(10, 103.6, rng))
    down = [[r[0], 200 - r[1], 200 - r[3], 200 - r[2], 200 - r[4], 10] for r in up]
    eu = r1.find_impulses(up, {"atr_n": 30})
    ed = r1.find_impulses(down, {"atr_n": 30})
    assert any(e["side"] == 1 for e in eu)
    assert [e["i_conf"] for e in ed if e["side"] == -1] == [e["i_conf"] for e in eu if e["side"] == 1]
    assert common.day_of(down[0][0]) == common.day_of(up[0][0])
