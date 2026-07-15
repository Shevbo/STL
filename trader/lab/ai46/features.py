"""Feature engine — faithful port of go-bot/internal/features/*.go.

Pure-float functions matching the Go originals 1:1 (no numpy, same edge cases).
Each function notes its Go source. Lists are Python lists of float; indexing and
integer division mirror Go semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════════════════════
#  indicators.go
# ════════════════════════════════════════════════════════════════════════════

def ema(prices: list[float], period: int) -> list[float] | None:
    """indicators.go::EMA — SMA-seeded EMA; None if len<period."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    out = [0.0] * len(prices)
    s = 0.0
    for i in range(period):
        s += prices[i]
    out[period - 1] = s / period
    for i in range(period, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


def last(s: list[float]) -> float:
    """indicators.go::Last."""
    return s[-1] if s else 0.0


def rsi(closes: list[float], period: int) -> float:
    """indicators.go::RSI — Wilder smoothing; 50 if insufficient data."""
    if len(closes) <= period:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        if d > 0:
            ag = (ag * (period - 1) + d) / period
            al = al * (period - 1) / period
        else:
            ag = ag * (period - 1) / period
            al = (al * (period - 1) - d) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> float:
    """indicators.go::VWAP."""
    cum_tv = cum_v = 0.0
    for i in range(len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_tv += tp * volumes[i]
        cum_v += volumes[i]
    return 0.0 if cum_v == 0 else cum_tv / cum_v


def volume_ratio(volumes: list[float], period: int) -> float:
    """indicators.go::VolumeRatio — current / mean(prev period)."""
    n = len(volumes)
    if n < period + 1:
        return 1.0
    cur = volumes[n - 1]
    s = sum(volumes[n - 1 - period:n - 1])
    avg = s / period
    return 1.0 if avg == 0 else cur / avg


# ════════════════════════════════════════════════════════════════════════════
#  ofi.go — order flow (needs live trades + orderbook)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    time: float   # unix seconds (data clock)
    side: str     # "buy" | "sell"
    volume: float


@dataclass
class BookLevel:
    price: float
    volume: float


@dataclass
class OrderBook:
    bids: list[BookLevel] = field(default_factory=list)  # best first
    asks: list[BookLevel] = field(default_factory=list)


@dataclass
class BufferStat:
    count: int = 0
    oldest_time: float = 0.0
    newest_time: float = 0.0
    last5m_count: int = 0


class TradeBuffer:
    """ofi.go::TradeBuffer — per-ticker sliding window of trades.

    `since` uses a DATA-clock cutoff (newest observed trade − window), not
    wall-clock, because MOEX ISS public trades lag ~15 min; a wall-clock cutoff
    would make the window permanently empty and OFI permanently 0.
    """

    def __init__(self, max_age_secs: float) -> None:
        self._trades: dict[str, list[Trade]] = {}
        self._max_age = max_age_secs

    def add(self, ticker: str, t: Trade, now: float) -> None:
        self._trades.setdefault(ticker, []).append(t)
        self._evict(ticker, now)

    def _evict(self, ticker: str, now: float) -> None:
        cutoff = now - self._max_age
        ts = self._trades.get(ticker, [])
        i = 0
        while i < len(ts) and ts[i].time < cutoff:
            i += 1
        self._trades[ticker] = ts[i:]

    def since(self, ticker: str, window_secs: float) -> list[Trade]:
        all_ = self._trades.get(ticker, [])
        if not all_:
            return []
        newest = all_[0].time
        for t in all_:
            if t.time > newest:
                newest = t.time
        cutoff = newest - window_secs
        return [t for t in all_ if t.time > cutoff]

    def stats(self, ticker: str, now: float) -> BufferStat:
        all_ = self._trades.get(ticker, [])
        s = BufferStat(count=len(all_))
        if not all_:
            return s
        s.oldest_time = all_[0].time
        s.newest_time = all_[0].time
        for t in all_:
            if t.time < s.oldest_time:
                s.oldest_time = t.time
            if t.time > s.newest_time:
                s.newest_time = t.time
        data_cutoff = s.newest_time - 300.0
        s.last5m_count = sum(1 for t in all_ if t.time > data_cutoff)
        return s


def ofi(tb: TradeBuffer, ticker: str, window_secs: float) -> float:
    """ofi.go::OFI = (buyVol-sellVol)/totalVol over window, in [-1,1]."""
    trades = tb.since(ticker, window_secs)
    buy_vol = sell_vol = 0.0
    for t in trades:
        if t.side == "buy":
            buy_vol += t.volume
        else:
            sell_vol += t.volume
    total = buy_vol + sell_vol
    return 0.0 if total == 0 else (buy_vol - sell_vol) / total


def mlofi(ob: OrderBook | None) -> float:
    """ofi.go::MLOFI = (bidVol-askVol)/totalVol across all levels."""
    if ob is None or not ob.bids or not ob.asks:
        return 0.0
    bid_vol = sum(b.volume for b in ob.bids)
    ask_vol = sum(a.volume for a in ob.asks)
    total = bid_vol + ask_vol
    return 0.0 if total == 0 else (bid_vol - ask_vol) / total


def queue_imbalance(ob: OrderBook | None) -> float:
    """ofi.go::QueueImbalance — best level (bid-ask)/(bid+ask)."""
    if ob is None or not ob.bids or not ob.asks:
        return 0.0
    b = ob.bids[0].volume
    a = ob.asks[0].volume
    return 0.0 if (b + a) == 0 else (b - a) / (b + a)


def microprice(ob: OrderBook | None) -> float:
    """ofi.go::Microprice — mid weighted by opposite queue."""
    if ob is None or not ob.bids or not ob.asks:
        return 0.0
    bid = ob.bids[0]
    ask = ob.asks[0]
    total = bid.volume + ask.volume
    if total == 0:
        return (bid.price + ask.price) / 2
    return (bid.price * ask.volume + ask.price * bid.volume) / total


def spread_bps(ob: OrderBook | None) -> float:
    """ofi.go::SpreadBPS — bid-ask spread in basis points (999 if unknown)."""
    if ob is None or not ob.bids or not ob.asks:
        return 999.0
    mid = (ob.bids[0].price + ob.asks[0].price) / 2
    if mid == 0:
        return 999.0
    return (ob.asks[0].price - ob.bids[0].price) / mid * 10000
