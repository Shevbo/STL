"""Lock _prev_day_hlc output: the perf rewrite must not change pivot results."""
from datetime import datetime, timezone

from trader.lab.runtime import Bar
from trader.lab.strategies import library


def _ts(day: int, minute: int) -> int:
    # 2026-06-<day> 10:00 + minute, stamped UTC (bars carry MSK-as-UTC)
    return int(datetime(2026, 6, day, 10, minute, tzinfo=timezone.utc).timestamp())


def _make_bars():
    bars = []
    # three calendar days, 5 bars each, distinct H/L/C per day
    for day in (10, 11, 12):
        for m in range(5):
            base = day * 100 + m
            bars.append(Bar(time=_ts(day, m), open=base, high=base + 10,
                            low=base - 10, close=base + 1, volume=100))
    return bars


def _reference_prev_day_hlc(bars):
    last_day = datetime.fromtimestamp(bars[-1].time, tz=timezone.utc).date()
    prev = [b for b in bars
            if datetime.fromtimestamp(b.time, tz=timezone.utc).date() < last_day]
    if not prev:
        return None
    last_prev_day = datetime.fromtimestamp(prev[-1].time, tz=timezone.utc).date()
    day = [b for b in prev
           if datetime.fromtimestamp(b.time, tz=timezone.utc).date() == last_prev_day]
    return max(b.high for b in day), min(b.low for b in day), day[-1].close


def test_prev_day_hlc_matches_reference_at_every_cursor():
    bars = _make_bars()
    # check across many window end points (mimic the sliding backtest cursor)
    for end in range(1, len(bars) + 1):
        window = bars[:end]
        assert library._prev_day_hlc(window) == _reference_prev_day_hlc(window)


def test_prev_day_hlc_none_when_single_day():
    bars = _make_bars()[:5]  # only day 10
    assert library._prev_day_hlc(bars) is None
