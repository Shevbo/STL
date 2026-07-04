from robot_runner.bars import BarBuilder, pick_price


def test_pick_price_fallback_chain():
    assert pick_price(100.0, 99.0, 101.0) == 100.0      # last wins
    assert pick_price(0.0, 87500.0, 87570.0) == 87535.0  # mid when no last
    assert pick_price(0.0, 87510.0, 0.0) == 87510.0      # bid-only feed
    assert pick_price(0.0, 0.0, 87570.0) == 87570.0      # ask-only
    assert pick_price(0.0, 0.0, 0.0) == 0.0              # nothing usable


def test_bar_builder_aggregates_minutes():
    b = BarBuilder()
    t0 = 1_751_500_020_000  # ms epoch, mid-minute
    m = 60_000
    # minute 0: three ticks
    b.on_tick(t0, 100.0)
    b.on_tick(t0 + 10_000, 105.0)
    b.on_tick(t0 + 30_000, 99.0)
    # minute 1 opens -> minute 0 closes
    b.on_tick(t0 + m, 101.0)
    bars = b.bars()
    assert len(bars) == 1
    bar = bars[0]
    assert bar.time == (t0 // m) * 60  # unix seconds, minute-truncated
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 105.0, 99.0, 99.0)
    # forming minute is NOT visible
    assert b.bars(1)[-1].time == bar.time


def test_bar_builder_gap_no_synthetic_and_caps():
    b = BarBuilder(max_bars=2)
    t0 = 1_751_500_000_000
    m = 60_000
    for i in range(4):
        b.on_tick(t0 + i * 2 * m, 100.0 + i)  # every other minute (gaps)
    assert len(b.bars()) == 2  # capped, no synthetic gap bars


def test_bar_builder_ignores_bad_and_late_ticks():
    b = BarBuilder()
    t0 = 1_751_500_000_000
    b.on_tick(t0, 0.0)          # zero price ignored
    b.on_tick(t0, 100.0)
    b.on_tick(t0 + 60_000, 101.0)   # closes minute 0
    b.on_tick(t0 - 60_000, 999.0)   # late tick from the past — ignored
    bars = b.bars()
    assert len(bars) == 1 and bars[0].close == 100.0
