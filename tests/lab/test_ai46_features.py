"""Fidelity tests for the team-46 feature port — assertions taken verbatim from
the go-bot test files (features/*_test.go), so a pass means the Python matches
the Go to the value."""
from trader.lab.ai46 import features as F


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
