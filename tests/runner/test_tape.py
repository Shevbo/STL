from robot_runner.bars import BarBuilder


def test_tape_builds_exact_ohlcv():
    b = BarBuilder()
    t0 = 1_783_250_000_000
    b.on_trade(t0, 89000.0, 2)
    b.on_trade(t0 + 5_000, 89050.0, 1)
    b.on_trade(t0 + 20_000, 88980.0, 3)
    b.on_trade(t0 + 61_000, 89010.0, 1)   # next minute closes the first
    bars = b.bars()
    assert len(bars) == 1
    bar = bars[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (89000.0, 89050.0, 88980.0, 88980.0)
    assert bar.volume == 6                 # true traded volume, not zero


def test_tape_mutes_snapshot_ticks():
    b = BarBuilder()
    t0 = 1_783_250_000_000
    b.on_trade(t0, 89000.0, 1)
    b.on_tick(t0 + 1_000, 88500.0)         # snapshot must NOT corrupt the tape bar
    b.on_trade(t0 + 61_000, 89010.0, 1)
    bar = b.bars()[0]
    assert bar.low == 89000.0              # 88500 mid never entered


def test_tick_fallback_after_tape_outage():
    b = BarBuilder()
    t0 = 1_783_250_000_000
    b.on_trade(t0, 89000.0, 1)
    late = t0 + BarBuilder.TAPE_PRIORITY_MS + 61_000
    b.on_tick(late, 88900.0)               # tape silent > priority window -> accepted
    assert b.bars()[-1].time == (t0 // 60_000) * 60
    assert b._cur is not None and b._cur.close == 88900.0
