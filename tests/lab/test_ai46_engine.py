"""Tests for the team-46 FeatureEngine."""
import random

from trader.lab.runtime import Bar
from trader.lab.ai46.engine import FeatureEngine, _aggregate, _direction


def _bars(n, seed=1, drift=0.0005):
    rng = random.Random(seed)
    # Align t0 to a day boundary so 1m bars bucket cleanly into 10m/1h/1d.
    t0, p = 1_700_000_000 - (1_700_000_000 % 86400), 100.0
    out = []
    for i in range(n):
        p2 = p * (1 + drift + rng.gauss(0, 0.001))
        out.append(Bar(time=t0 + i * 60, open=p, high=max(p, p2) + 0.05,
                       low=min(p, p2) - 0.05, close=p2, volume=1000 + rng.randint(0, 200)))
        p = p2
    return out


def test_aggregate_buckets_10m():
    bars = _bars(30)
    h, lo, c, v = _aggregate(bars, 600)   # 30 1m bars → 3 ten-minute buckets
    assert len(c) == 3
    assert v[0] == sum(b.volume for b in bars[:10])
    assert c[0] == bars[9].close
    assert h[0] == max(b.high for b in bars[:10])


def test_direction_rule():
    # rising but not overbought → long; need ema9>ema21 and rsi<70
    closes = [100 + i * 0.1 + (i % 3) for i in range(40)]
    d = _direction(closes, closes, closes)
    assert d in ("long", "flat")
    assert _direction([1, 2], [1, 2], [1, 2]) == "flat"   # too short → flat


def test_compute_assembles_features():
    fe = FeatureEngine()
    mf = fe.compute("RIU6", _bars(300))
    assert mf is not None
    assert set(mf.directions) == {"1d", "1h", "10m", "1m"}
    assert mf.hmm_state in ("trend_up", "trend_down", "flat", "panic")
    assert mf.garch_vol >= 0.0
    assert 0.0 <= mf.agreement_ratio("long") <= 1.0
    assert mf.vwap > 0.0
    assert mf.volume_ratio > 0.0
