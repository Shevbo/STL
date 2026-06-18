"""Fidelity tests for the team-46 feature port — assertions taken verbatim from
the go-bot test files (features/*_test.go), so a pass means the Python matches
the Go to the value."""
import math

from trader.lab.ai46 import features as F


# ── pivot_points_test.go ──────────────────────────────────────────────────────

def test_classic_pivots_known_values():
    pp = F.classic_pivots(110, 100, 105)  # H=110 L=100 C=105 → P=105
    assert pp.p == 105
    assert pp.r1 == 110
    assert pp.s1 == 100
    assert pp.r2 == 115
    assert pp.s2 == 95
    assert pp.r3 == 120
    assert pp.s3 == 90


def test_classic_pivots_zero_safe():
    pp = F.classic_pivots(0, 0, 0)
    assert pp.p == 0 and pp.r1 == 0 and pp.s3 == 0


# ── stochastic_test.go ────────────────────────────────────────────────────────

def test_stochastic_rising_series_high_k():
    highs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    lows = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    closes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    k, d = F.stochastic(highs, lows, closes, 14, 3)
    assert k >= 95
    assert d >= 80


def test_stochastic_falling_series_low_k():
    highs = [16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    lows = [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    closes = [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    k, _ = F.stochastic(highs, lows, closes, 14, 3)
    assert k <= 5


def test_stochastic_not_enough_data():
    k, d = F.stochastic([1, 2], [1, 2], [1, 2], 14, 3)
    assert math.isnan(k) and math.isnan(d)


# ── ict_structure_test.go ─────────────────────────────────────────────────────

def test_fvg_bullish_gap():
    fvgs = F.fair_value_gaps([100, 101, 104], [99, 100, 103], 10)
    assert len(fvgs) == 1
    assert fvgs[0].direction == "bullish"
    assert fvgs[0].low == 100 and fvgs[0].high == 103


def test_fvg_bearish_gap():
    fvgs = F.fair_value_gaps([104, 102, 99], [103, 101, 98], 10)
    assert len(fvgs) == 1 and fvgs[0].direction == "bearish"
    assert fvgs[0].low == 99 and fvgs[0].high == 103


def test_order_blocks_last_bearish_candle_before_impulse():
    opens = [100, 100, 99.5]
    highs = [101, 100.5, 105]
    lows = [99, 99, 99.5]
    closes = [100, 99.5, 104.8]
    obs = F.order_blocks(opens, highs, lows, closes, 0.04)
    assert len(obs) == 1 and obs[0].direction == "bullish"
    assert obs[0].high == 100.5 and obs[0].low == 99


# ── ofi.go (order flow) ───────────────────────────────────────────────────────

def test_ofi_buy_pressure():
    tb = F.TradeBuffer(max_age_secs=3600)
    for i in range(5):
        tb.add("RIU6", F.Trade(time=1000.0 + i, side="buy", volume=10), now=1000.0 + i)
    tb.add("RIU6", F.Trade(time=1005.0, side="sell", volume=10), now=1005.0)
    # 50 buy vs 10 sell over the window → (50-10)/60
    assert abs(F.ofi(tb, "RIU6", 300) - (40 / 60)) < 1e-9


def test_queue_imbalance_and_spread():
    ob = F.OrderBook(bids=[F.BookLevel(99, 30)], asks=[F.BookLevel(101, 10)])
    assert abs(F.queue_imbalance(ob) - (20 / 40)) < 1e-9
    assert abs(F.spread_bps(ob) - ((101 - 99) / 100 * 10000)) < 1e-9


def test_volume_profile_poc_on_concentrated_band():
    # All volume traded in a tight band around 100 → POC near 100.
    highs = [100.2, 100.1, 100.2, 100.1, 100.2, 105.0]
    lows = [99.8, 99.9, 99.8, 99.9, 99.8, 95.0]
    closes = [100, 100, 100, 100, 100, 100]
    vols = [100, 100, 100, 100, 100, 1]
    vp = F.volume_profile(highs, lows, closes, vols, 20)
    assert vp.poc > 0
    assert vp.val <= vp.poc <= vp.vah
