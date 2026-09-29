"""A5 на синтетике: минуты дня с систематическим ходом (реальных данных здесь нет)."""
import json
import random
from datetime import datetime, timezone

from trader.lab.footprints import a5_time_of_day as a5
from trader.lab.footprints import common

D0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())   # метка = МСК-стенка


def _bars(n_days=30, seed=1, drift_minute=None, drift_pts=4.0, noise=5.0,
          m_lo=540, m_hi=1430):
    """Случайное блуждание 09:00-23:49 (клиринги НЕ выбрасываются, это дело A5);
    drift_minute -- минута m: ход close[m]-close[m-1] получает +drift_pts каждый
    день, то есть move(m-1, h=1) = close[m]-close[m-1] систематически смещён."""
    rng = random.Random(seed)
    rows, px = [], 100000.0
    for d in range(n_days):
        for m in range(m_lo, m_hi):
            o = px
            px += rng.gauss(0, noise)
            if drift_minute is not None and m == drift_minute + 1:
                px += drift_pts
            v = rng.lognormvariate(3, 0.5)
            rows.append([D0 + d * 86400 + m * 60, o, max(o, px) + 1, min(o, px) - 1, px, v])
    return rows


def _row(res, minute, h):
    return next((r for r in res["rows"] if r["minute"] == minute and r["h"] == h), None)


def test_random_walk_top_minute_not_significant():
    """(1) Случайное блуждание: самая экстремальная минута не проходит поправку на перебор."""
    res = a5.analyze(_bars(n_days=30, seed=1), horizons=(1,), named=(), draws=100, seed=0)
    top = max(res["rows"], key=lambda r: abs(r["t"]))
    assert top["p_adj"] > 0.05


def test_scheduled_drift_named_flagged_other_minute_not():
    """(2) Дрейф +4пт в минуту 16:30 (990) каждый день: она значима, случайная минута -- нет."""
    rows = _bars(n_days=40, seed=2, drift_minute=990, drift_pts=6.0, noise=4.0)
    probe = a5.NAMED_MINUTES + (700,)
    res = a5.analyze(rows, horizons=(1,), named=probe, draws=120, seed=0)

    hit = _row(res, 990, 1)
    assert hit is not None
    assert hit["p_named"] < 0.01
    assert hit["p_adj"] < 0.05
    assert hit["mean"] > 0

    other = _row(res, 700, 1)
    assert other is not None
    assert other["p_named"] > 0.05


def test_window_beyond_day_end_excluded():
    """(3) Минута 23:45 (1425): h=1 входит в день, h=5 и h=15 выходят за 23:49 -- не считаются."""
    rows = _bars(n_days=5, seed=3, m_lo=1400, m_hi=1430)
    res = a5.analyze(rows, horizons=(1, 5, 15), named=(1425,), draws=20, seed=0)
    assert _row(res, 1425, 1) is not None
    assert _row(res, 1425, 5) is None
    assert _row(res, 1425, 15) is None


def test_report_json_safe():
    res = a5.analyze(_bars(n_days=4, seed=4), horizons=(1,), named=a5.NAMED_MINUTES, draws=10)
    rep = common.report("A5", "RIZ6", ["2026-09-01", None], res["rows"], res["notes"],
                        n_days=res["n_days"], halves=res["halves"],
                        cost_pts=common.round_trip_cost_pts("RIZ6"), atr_min_pts=None)
    json.dumps(rep, allow_nan=False)
    assert set(rep) == {"id", "symbol", "window", "n_days", "rows", "halves",
                        "cost_pts", "atr_min_pts", "notes"}
