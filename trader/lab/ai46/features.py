"""Feature engine — faithful port of go-bot/internal/features/*.go.

Pure-float functions matching the Go originals 1:1 (no numpy, same edge cases).
Each function notes its Go source. Lists are Python lists of float; indexing and
integer division mirror Go semantics.
"""
from __future__ import annotations

import math
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


@dataclass
class MACDResult:
    macd: float = 0.0
    signal: float = 0.0
    hist: float = 0.0


def macd(closes: list[float], fast: int, slow: int, signal: int) -> MACDResult:
    """indicators.go::MACD."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return MACDResult()
    macd_line = [0.0] * len(closes)
    for i in range(slow - 1, len(closes)):
        macd_line[i] = ema_fast[i] - ema_slow[i]
    sig = ema(macd_line[slow - 1:], signal)
    if sig is None:
        return MACDResult()
    m = last(macd_line)
    s = last(sig)
    return MACDResult(macd=m, signal=s, hist=m - s)


@dataclass
class BB:
    upper: float = 0.0
    mid: float = 0.0
    lower: float = 0.0
    position: float = 0.0  # (close-lower)/(upper-lower)
    width: float = 0.0     # (upper-lower)/mid


def bollinger_bands(closes: list[float], period: int, mult: float) -> BB:
    """indicators.go::BollingerBands — population std (÷period)."""
    if len(closes) < period:
        return BB()
    window = closes[len(closes) - period:]
    mid = sum(window) / period
    variance = 0.0
    for c in window:
        d = c - mid
        variance += d * d
    std = math.sqrt(variance / period)
    upper = mid + mult * std
    lower = mid - mult * std
    close = closes[-1]
    pos = 0.0
    if upper != lower:
        pos = (close - lower) / (upper - lower)
    return BB(upper=upper, mid=mid, lower=lower, position=pos,
              width=(upper - lower) / mid)


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """indicators.go::ATR — Wilder smoothing."""
    n = len(closes)
    if n < period + 1:
        return 0.0
    trs = [0.0] * (n - 1)
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        trs[i - 1] = max(hl, hc, lc)
    a = 0.0
    for i in range(period):
        a += trs[i]
    a /= period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


def adx(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """indicators.go::ADX — Wilder DM/TR smoothing."""
    n = len(closes)
    if n < period * 2:
        return 0.0
    dm_plus = [0.0] * (n - 1)
    dm_minus = [0.0] * (n - 1)
    trs = [0.0] * (n - 1)
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        if up_move > down_move and up_move > 0:
            dm_plus[i - 1] = up_move
        if down_move > up_move and down_move > 0:
            dm_minus[i - 1] = down_move
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        trs[i - 1] = max(hl, hc, lc)

    def smooth(vals: list[float]) -> list[float]:
        out = [0.0] * len(vals)
        s = 0.0
        for i in range(period):
            s += vals[i]
        out[period - 1] = s
        for i in range(period, len(vals)):
            out[i] = out[i - 1] - out[i - 1] / period + vals[i]
        return out

    s_tr = smooth(trs)
    s_dmp = smooth(dm_plus)
    s_dmm = smooth(dm_minus)
    dx: list[float] = []
    for i in range(period - 1, len(s_tr)):
        if s_tr[i] == 0:
            continue
        di_p = 100 * s_dmp[i] / s_tr[i]
        di_m = 100 * s_dmm[i] / s_tr[i]
        ssum = di_p + di_m
        dx.append(0.0 if ssum == 0 else 100 * abs(di_p - di_m) / ssum)
    if len(dx) < period:
        return 0.0
    a = 0.0
    for i in range(period):
        a += dx[i]
    a /= period
    for i in range(period, len(dx)):
        a = (a * (period - 1) + dx[i]) / period
    return a


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


def realized_vol(closes: list[float], period: int) -> float:
    """indicators.go::RealizedVol — annualised (×sqrt(252·6.5·60))."""
    n = len(closes)
    if n < period + 1:
        return 0.0
    rets = [0.0] * period
    for i in range(period):
        c = closes[n - period - 1 + i]
        nxt = closes[n - period + i]
        if c > 0:
            rets[i] = math.log(nxt / c)
    mean = sum(rets) / period
    variance = 0.0
    for r in rets:
        d = r - mean
        variance += d * d
    return math.sqrt(variance / period) * math.sqrt(252 * 6.5 * 60)


# ─── Kakushadze alpha factors ────────────────────────────────────────────────

def alpha6(opens: list[float], volumes: list[float]) -> float:
    """indicators.go::Alpha6 = -corr(open, volume, 10)."""
    return -ts_correlation(opens, volumes, 10)


def alpha12(closes: list[float], volumes: list[float]) -> float:
    """indicators.go::Alpha12 = sign(Δvol) · (-Δclose)."""
    n = len(closes)
    if n < 2:
        return 0.0
    dv = volumes[n - 1] - volumes[n - 2]
    dc = closes[n - 1] - closes[n - 2]
    return math.copysign(1, dv) * (-dc)


def alpha41(highs: list[float], lows: list[float], closes: list[float], vols: list[float]) -> float:
    """indicators.go::Alpha41 = sqrt(high·low) - vwap."""
    n = len(closes)
    if n == 0:
        return 0.0
    return math.sqrt(highs[n - 1] * lows[n - 1]) - vwap(highs, lows, closes, vols)


def alpha101(opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> float:
    """indicators.go::Alpha101 = (close-open)/(high-low+eps)."""
    n = len(closes)
    if n == 0:
        return 0.0
    return (closes[n - 1] - opens[n - 1]) / (highs[n - 1] - lows[n - 1] + 0.001)


def ts_correlation(xs: list[float], ys: list[float], period: int) -> float:
    """indicators.go::tsCorrelation — Pearson over last `period`."""
    n = len(xs)
    if n < period or len(ys) < period:
        return 0.0
    x = xs[n - period:]
    y = ys[n - period:]
    mx = sum(x[:period]) / period
    my = sum(y[:period]) / period
    num = dx2 = dy2 = 0.0
    for i in range(period):
        dx = x[i] - mx
        dy = y[i] - my
        num += dx * dy
        dx2 += dx * dx
        dy2 += dy * dy
    denom = math.sqrt(dx2 * dy2)
    return 0.0 if denom == 0 else num / denom


def cross_asset_correlation(global_: list[float], moex: list[float], lags: list[int]) -> tuple[int, float]:
    """indicators.go::CrossAssetCorrelation — lag with max |corr|."""
    best_lag = 0
    best_corr = 0.0
    for lag in lags:
        if lag >= len(moex) or lag >= len(global_):
            continue
        shifted = moex[lag:]
        g = global_[:len(shifted)]
        period = len(shifted)
        if period < 10:
            continue
        corr = ts_correlation(g, shifted, period)
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag
    return best_lag, best_corr


# ════════════════════════════════════════════════════════════════════════════
#  stochastic.go
# ════════════════════════════════════════════════════════════════════════════

def stochastic(highs: list[float], lows: list[float], closes: list[float],
               period: int, smooth: int) -> tuple[float, float]:
    """stochastic.go::Stochastic — slow %K, %D=SMA(%K, smooth). NaN if short."""
    if period <= 0 or smooth <= 0 or len(closes) < period + smooth - 1:
        return math.nan, math.nan
    k_series: list[float] = []
    for i in range(len(closes) - smooth, len(closes)):
        hi, lo = highs[i - period + 1], lows[i - period + 1]
        for j in range(i - period + 2, i + 1):
            if highs[j] > hi:
                hi = highs[j]
            if lows[j] < lo:
                lo = lows[j]
        rng = hi - lo
        k = 50.0 if rng <= 0 else 100.0 * (closes[i] - lo) / rng
        k_series.append(k)
    d = sum(k_series) / len(k_series)
    return k_series[-1], d


# ════════════════════════════════════════════════════════════════════════════
#  volume_profile.go
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class VPLevels:
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0


def volume_profile(highs: list[float], lows: list[float], closes: list[float],
                   volumes: list[float], n_buckets: int) -> VPLevels:
    """volume_profile.go::VolumeProfile — POC + 70% value area."""
    if (n_buckets < 5 or len(closes) < 5 or len(highs) != len(closes)
            or len(lows) != len(closes) or len(volumes) != len(closes)):
        return VPLevels()
    hi, lo = highs[0], lows[0]
    for i in range(len(highs)):
        if highs[i] > hi:
            hi = highs[i]
        if lows[i] < lo:
            lo = lows[i]
    rng = hi - lo
    if rng <= 0:
        return VPLevels()
    bucket_size = rng / n_buckets
    buckets = [0.0] * n_buckets

    def bucket_price(i: int) -> float:
        return lo + (i + 0.5) * bucket_size

    for i in range(len(highs)):
        bar_lo, bar_hi, vol = lows[i], highs[i], volumes[i]
        if bar_hi <= bar_lo or vol <= 0:
            continue
        start_idx = int((bar_lo - lo) / bucket_size)
        end_idx = int((bar_hi - lo) / bucket_size)
        # Top edge exclusive (matches Go float-boundary check).
        if end_idx > start_idx and end_idx * bucket_size + lo == bar_hi:
            end_idx -= 1
        if end_idx >= n_buckets:
            end_idx = n_buckets - 1
        if start_idx < 0:
            start_idx = 0
        span = end_idx - start_idx + 1
        per = vol / span
        for b in range(start_idx, end_idx + 1):
            buckets[b] += per

    poc_idx = 0
    max_vol = buckets[0]
    total = 0.0
    for i, v in enumerate(buckets):
        total += v
        if v > max_vol:
            max_vol = v
            poc_idx = i
    if total <= 0:
        return VPLevels()
    target = total * 0.70
    acc = buckets[poc_idx]
    low_idx = high_idx = poc_idx
    while acc < target and (low_idx > 0 or high_idx < n_buckets - 1):
        left_vol = buckets[low_idx - 1] if low_idx > 0 else 0.0
        right_vol = buckets[high_idx + 1] if high_idx < n_buckets - 1 else 0.0
        if left_vol >= right_vol and low_idx > 0:
            low_idx -= 1
            acc += left_vol
        elif high_idx < n_buckets - 1:
            high_idx += 1
            acc += right_vol
        elif low_idx > 0:
            low_idx -= 1
            acc += left_vol
    return VPLevels(poc=bucket_price(poc_idx), vah=bucket_price(high_idx), val=bucket_price(low_idx))


# ════════════════════════════════════════════════════════════════════════════
#  support_resistance.go
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Level:
    price: float
    touches: int
    score: float


@dataclass
class PivotSample:
    price: float
    age_bars: int
    volume_weight: float


@dataclass
class SRSource:
    highs: list[float]
    lows: list[float]
    window: int
    volumes: list[float] | None = None


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    cp = sorted(xs)
    n = len(cp)
    if n % 2 == 1:
        return cp[n // 2]
    return (cp[n // 2 - 1] + cp[n // 2]) / 2


def _pivot_samples(series: list[float], volumes: list[float] | None,
                   window: int, is_high: bool) -> list[PivotSample]:
    """support_resistance.go::pivotSamples."""
    if len(series) < 2 * window + 1:
        return []
    median_vol = _median(volumes) if (volumes is not None and len(volumes) == len(series)) else 0.0
    out: list[PivotSample] = []
    n = len(series)
    for i in range(window, n - window):
        v = series[i]
        extremum = True
        for j in range(i - window, i + window + 1):
            if j == i:
                continue
            if is_high and series[j] > v:
                extremum = False
                break
            if not is_high and series[j] < v:
                extremum = False
                break
        if not extremum:
            continue
        vol_w = 1.0
        if median_vol > 0 and volumes is not None and i < len(volumes):
            vol_w = volumes[i] / median_vol
            if vol_w < 0.25:
                vol_w = 0.25
            elif vol_w > 4:
                vol_w = 4.0
        out.append(PivotSample(price=v, age_bars=n - 1 - i, volume_weight=vol_w))
    return out


def _score_clusters(samples: list[PivotSample], cluster_pct: float, series_len: int) -> list[Level]:
    """support_resistance.go::scoreClusters — recency×volume scoring."""
    if not samples:
        return []
    samples = sorted(samples, key=lambda s: s.price)
    horizon = series_len
    if horizon <= 0:
        for s in samples:
            if s.age_bars > horizon:
                horizon = s.age_bars
    if horizon < 1:
        horizon = 1

    clusters: list[dict] = [{"samples": [samples[0]], "anchor": samples[0].price}]
    for s in samples[1:]:
        c = clusters[-1]
        if c["anchor"] == 0 or abs(s.price - c["anchor"]) / c["anchor"] <= cluster_pct:
            c["samples"].append(s)
            c["anchor"] = sum(x.price for x in c["samples"]) / len(c["samples"])
        else:
            clusters.append({"samples": [s], "anchor": s.price})

    out: list[Level] = []
    for c in clusters:
        score = 0.0
        for s in c["samples"]:
            recency = math.exp(-s.age_bars / horizon)
            score += recency * s.volume_weight
        out.append(Level(price=c["anchor"], touches=len(c["samples"]), score=score))
    out.sort(key=lambda lv: lv.score, reverse=True)
    return out


def support_resistance(highs: list[float], lows: list[float], window: int,
                       cluster_pct: float) -> tuple[list[float], list[float]]:
    """support_resistance.go::SupportResistance — resistance desc, support asc."""
    if window <= 0 or len(highs) < 2 * window + 1 or len(highs) != len(lows):
        return [], []
    hi_samples = _pivot_samples(highs, None, window, True)
    lo_samples = _pivot_samples(lows, None, window, False)
    res_lvls = _score_clusters(hi_samples, cluster_pct, len(highs))
    sup_lvls = _score_clusters(lo_samples, cluster_pct, len(lows))
    resistance = sorted((lv.price for lv in res_lvls), reverse=True)
    support = sorted(lv.price for lv in sup_lvls)
    return resistance, support


def support_resistance_multi(sources: list[SRSource], cluster_pct: float,
                             top_k: int) -> tuple[list[Level], list[Level]]:
    """support_resistance.go::SupportResistanceMulti — multi-TF top-K levels."""
    hi: list[PivotSample] = []
    lo: list[PivotSample] = []
    for src in sources:
        if src.window <= 0 or len(src.highs) < 2 * src.window + 1 or len(src.highs) != len(src.lows):
            continue
        hi += _pivot_samples(src.highs, src.volumes, src.window, True)
        lo += _pivot_samples(src.lows, src.volumes, src.window, False)
    res_lvls = _score_clusters(hi, cluster_pct, 0)
    sup_lvls = _score_clusters(lo, cluster_pct, 0)
    if top_k > 0:
        res_lvls = res_lvls[:top_k]
        sup_lvls = sup_lvls[:top_k]
    res_lvls.sort(key=lambda lv: lv.price, reverse=True)
    sup_lvls.sort(key=lambda lv: lv.price)
    return res_lvls, sup_lvls


# ════════════════════════════════════════════════════════════════════════════
#  ict_structure.go
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class FVGItem:
    direction: str   # "bullish" | "bearish"
    low: float
    high: float
    bar_index: int


@dataclass
class OrderBlockItem:
    direction: str   # "bullish" | "bearish"
    low: float
    high: float
    bar_index: int


def fair_value_gaps(highs: list[float], lows: list[float], max_out: int) -> list[FVGItem]:
    """ict_structure.go::FairValueGaps — up to max_out newest-first 3-bar gaps."""
    if len(highs) < 3 or len(lows) != len(highs) or max_out <= 0:
        return []
    out: list[FVGItem] = []
    for i in range(len(highs) - 1, 1, -1):
        if lows[i] > highs[i - 2]:
            out.append(FVGItem("bullish", highs[i - 2], lows[i], i))
        elif highs[i] < lows[i - 2]:
            out.append(FVGItem("bearish", highs[i], lows[i - 2], i))
        if len(out) >= max_out:
            break
    return out


def order_blocks(opens: list[float], highs: list[float], lows: list[float],
                 closes: list[float], impulse_frac: float) -> list[OrderBlockItem]:
    """ict_structure.go::OrderBlocks — impulse + last opposing candle, newest-first."""
    if (len(closes) < 2 or len(opens) != len(closes)
            or len(highs) != len(closes) or len(lows) != len(closes)):
        return []
    out: list[OrderBlockItem] = []
    for i in range(1, len(closes)):
        if closes[i] <= 0:
            continue
        body = closes[i] - opens[i]
        mag = body / closes[i]
        if mag >= impulse_frac:
            for j in range(i - 1, -1, -1):
                if closes[j] < opens[j]:
                    out.append(OrderBlockItem("bullish", lows[j], highs[j], j))
                    break
        elif -mag >= impulse_frac:
            for j in range(i - 1, -1, -1):
                if closes[j] > opens[j]:
                    out.append(OrderBlockItem("bearish", lows[j], highs[j], j))
                    break
    out.reverse()
    return out


# ════════════════════════════════════════════════════════════════════════════
#  pivot_points.go
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class PivotLevels:
    p: float
    r1: float
    s1: float
    r2: float
    s2: float
    r3: float
    s3: float


def classic_pivots(prev_high: float, prev_low: float, prev_close: float) -> PivotLevels:
    """pivot_points.go::ClassicPivots — full floor-trader set incl R3/S3."""
    p = (prev_high + prev_low + prev_close) / 3.0
    rng = prev_high - prev_low
    return PivotLevels(
        p=p,
        r1=2 * p - prev_low,
        s1=2 * p - prev_high,
        r2=p + rng,
        s2=p - rng,
        r3=prev_high + 2 * (p - prev_low),
        s3=prev_low - 2 * (prev_high - p),
    )


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
