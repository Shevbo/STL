"""1-minute bar aggregation from local QUIK DDE ticks.

Strategies consume CLOSED bars only (the forming minute is invisible) so a
signal computed mid-minute cannot repaint — matching backtest semantics where
on_bar sees completed candles.
"""

from collections import deque

from trader.lab.runtime import Bar


def pick_price(last: float, bid: float, ask: float) -> float:
    """Bar price from a tick: trade price when the feed carries it, else the
    mid, else whichever side exists. The QUIK DDE export may lack the
    last-price column (params sheet has Спрос/Предл. only) — without this
    fallback no bars would ever build. Returns 0.0 when nothing usable."""
    if last > 0:
        return last
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return bid if bid > 0 else (ask if ask > 0 else 0.0)


class BarBuilder:
    def __init__(self, max_bars: int = 3000) -> None:
        self._bars: deque[Bar] = deque(maxlen=max_bars)
        self._cur: Bar | None = None   # forming minute

    def on_tick(self, ts_ms: int, last: float) -> None:
        if last <= 0:
            return
        minute = int(ts_ms // 60_000) * 60  # unix seconds
        cur = self._cur
        if cur is None or minute > cur.time:
            if cur is not None:
                self._bars.append(cur)   # close the previous minute
            self._cur = Bar(time=minute, open=last, high=last, low=last,
                            close=last, volume=0)
            return
        if minute < cur.time:
            return  # late tick from a closed minute — ignore
        cur.high = max(cur.high, last)
        cur.low = min(cur.low, last)
        cur.close = last

    def bars(self, n: int = 0) -> list[Bar]:
        out = list(self._bars)
        return out[-n:] if n else out

    @property
    def last_bar_time(self) -> int:
        return self._bars[-1].time if self._bars else 0
