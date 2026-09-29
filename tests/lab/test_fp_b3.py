"""B3 на синтетике (реальных данных здесь нет, см. зону backtests)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import b3_round_levels as b3

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())   # метка = МСК-стенка
STEP = 500.0


def _row(ts, px):
    """OHLCV с постоянным H-L=4 -> ATR минуты фиксирован (не зависит от хода цены)."""
    return [ts, px, px + 2, px - 2, px, 1.0]


def _walk_bars(n_days, seed, bars_per_day=300, std=15.0, bias=None):
    """Случайное блуждание; bias=(eps, prob) на заходе в eps от круглого уровня
    STEP с вероятностью prob толкает цену назад, откуда пришла (гистерезис
    far/near как в b3.approaches, d=4*eps, что совпадает с eps=0.5*ATR и
    d=2*ATR при постоянном H-L=4 в _row)."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    near, tracked, away_dir, prev_px = False, None, 0.0, px
    d = bias[0] * 4 if bias else None
    for day in range(n_days):
        for m in range(600, 600 + bars_per_day):
            if bias:
                eps, prob = bias
                if not near:
                    lvl = round(px / STEP) * STEP
                    if abs(px - lvl) <= eps:
                        away_dir = -1.0 if prev_px < lvl else 1.0
                        near, tracked = True, lvl
                elif abs(px - tracked) > d:
                    near, tracked = False, None
                prev_px = px
                if near and rng.random() < prob:
                    px += away_dir * (abs(rng.gauss(0, std)) + 1.0)
                else:
                    px += rng.gauss(0, std)
            else:
                px += rng.gauss(0, std)
            rows.append(_row(D0 + day * 86400 + m * 60, px))
    return rows


def test_random_walk_no_effect():
    rows = _walk_bars(n_days=20, seed=1)
    res = b3.analyze(rows, steps=(STEP,), horizon=30, draws=60, seed=0)
    for row in res["rows"]:
        nr = row["null_random"]
        assert nr["bounce"]["p"] > 0.05, row["direction"]
        assert nr["pinning"]["p"] > 0.05, row["direction"]


def test_bias_gives_signal_at_round_level_not_pseudo():
    eps, prob = 2.0, 0.8   # eps совпадает с 0.5*ATR при H-L=4 (см. _row)
    rows = _walk_bars(n_days=25, seed=2, bias=(eps, prob))
    # draws>=100 обязателен: p-value ограничен снизу 1/(draws+1) (common.pvalue_and_ci).
    res = b3.analyze(rows, steps=(STEP,), horizon=30, draws=200, seed=0)
    for row in res["rows"]:
        assert row["n"] >= 10, row["direction"]
        assert row["null_random"]["bounce"]["p"] < 0.01, row["direction"]
        # у псевдоуровня (шаг/2) отскока не подмешивали — контроль заметно ниже реального
        assert row["null_half"]["p_bounce"] < row["p_bounce"] - 0.15, row["direction"]


def test_approach_once_per_visit():
    eps, d = 5.0, 20.0
    prices = [980, 990, 998, 1000, 1002, 999, 970, 940, 960, 985, 997, 1001, 998]
    day_rows = [_row(D0 + m * 60, p) for m, p in enumerate(prices)]
    events = b3.approaches(day_rows, STEP, 0.0, eps, d)
    assert [e[1] for e in events] == [1000.0, 1000.0]
    assert [e[0] for e in events] == [2, 10]
    assert len(events) == 2   # не по бару (баров у уровня 1000 гораздо больше двух)


def test_steps_for_defaults_by_prefix():
    assert b3.steps_for("RIZ6") == (500, 1000)
    assert b3.steps_for("SiZ6") == (500, 1000)
    assert b3.steps_for("GDZ6") == (10, 50)
    assert b3.steps_for("BRZ6") == (0.5, 1)
    assert b3.steps_for("XYZ6", steps=[7, 11]) == (7, 11)
    try:
        b3.steps_for("XYZ6")
        assert False, "должен упасть без явных steps"
    except ValueError:
        pass
