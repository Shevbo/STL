"""Tests for the team-46 order-flow collector (pure part, no gRPC)."""
from trader.lab.ai46.order_flow import OrderFlow, _SIDE_BUY, _SIDE_SELL


def test_ofi_explicit_sides():
    of = OrderFlow()
    t0 = 1_000_000.0
    for i in range(4):
        of.on_trade("RIU6", t0 + i, price=100 + i, size=10, side_enum=_SIDE_BUY)
    of.on_trade("RIU6", t0 + 5, price=100, size=20, side_enum=_SIDE_SELL)
    # buy 40 vs sell 20 → (40-20)/60
    assert abs(of.ofi("RIU6", window_secs=300) - (20 / 60)) < 1e-9


def test_tick_rule_infers_side_when_unspecified():
    of = OrderFlow()
    t0 = 2_000_000.0
    # rising prices, no side given → all classified buy → OFI = +1
    for i in range(5):
        of.on_trade("SiU6", t0 + i, price=100 + i, size=5, side_enum=0)
    assert of.ofi("SiU6") == 1.0
    # Falling prices: seed an explicit first trade so there's a price reference
    # (the very first trade has no prior price and the tick rule defaults to buy).
    of2 = OrderFlow()
    of2.on_trade("SiU6", t0, price=100, size=5, side_enum=_SIDE_SELL)
    for i in range(1, 5):
        of2.on_trade("SiU6", t0 + i, price=100 - i, size=5, side_enum=0)
    assert of2.ofi("SiU6") == -1.0


def test_ofi_window_excludes_old_trades():
    of = OrderFlow()
    of.on_trade("BRU6", 0.0, price=50, size=100, side_enum=_SIDE_SELL)   # far in the past
    of.on_trade("BRU6", 10_000.0, price=51, size=10, side_enum=_SIDE_BUY)  # newest
    # window 300s from newest (10000) → only the buy counts → +1
    assert of.ofi("BRU6", window_secs=300) == 1.0


def test_book_features():
    of = OrderFlow()
    of.on_book("GZU6", bids=[(99.0, 30.0), (98.0, 10.0)], asks=[(101.0, 10.0), (102.0, 5.0)])
    assert abs(of.queue_imbalance("GZU6") - (20 / 40)) < 1e-9          # (30-10)/(30+10)
    assert abs(of.mlofi("GZU6") - ((40 - 15) / 55)) < 1e-9             # all levels
    assert abs(of.spread_bps("GZU6") - ((101 - 99) / 100 * 10000)) < 1e-9
    # microprice = (bid*askVol + ask*bidVol)/(bidVol+askVol)
    assert abs(of.microprice("GZU6") - ((99 * 10 + 101 * 30) / 40)) < 1e-9


def test_snapshot_keys_and_empty_defaults():
    of = OrderFlow()
    snap = of.snapshot("UNKNOWN")
    assert set(snap) == {"ofi", "mlofi", "queue_imbalance", "microprice", "spread_bps"}
    assert snap["ofi"] == 0.0 and snap["spread_bps"] == 999.0  # no data → safe defaults
