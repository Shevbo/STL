"""Event detector for team-46 — port of go-bot/internal/event/detector.go.

Pure decision logic adapted to a tick/step model (the Go version runs a 5s
goroutine; here checkTicker is called per evaluation with an injected `now`).
Thresholds, EWMA price-shock, categories and the per-(ticker,type) emit
cooldown match the Go original 1:1.
"""
from __future__ import annotations

from dataclasses import dataclass

_EMIT_COOLDOWN = 300.0  # 5 min per (ticker, type), matches emitCooldown
_PRICE_EWMA_ALPHA = 0.1

# EventType (detector.go)
NEWS = "news"
PRICE_SHOCK = "price_shock"
VOL_SPIKE = "volume_spike"
OFI_ANOMALY = "ofi_anomaly"
CROSS_ASSET = "cross_asset"
TREND_FLIP = "trend_flip"

# EventCategory
NORMAL = "normal"
UNCERTAIN = "uncertain"
BLACK_SWAN = "black_swan"


@dataclass
class TickerFeatures:
    """Subset of features.TickerFeatures the detector reads."""
    ofi5m: float = 0.0
    volume_ratio: float = 1.0
    price_change_1m: float = 0.0     # Signals["1m"].PriceChangePct
    hmm_state: str = "flat"
    cross_asset_signal: str = ""      # "bullish" | "bearish" | ""
    dir_1h: str = "flat"              # Signals["1h"].Direction
    dir_10m: str = "flat"             # Signals["10m"].Direction
    tf_agreement: float = 0.0         # AgreementRatio(inferred dir)


@dataclass
class Signal:
    ticker: str
    type: str
    category: str
    ofi: float = 0.0
    volume_ratio: float = 0.0
    price_change: float = 0.0
    sigma: float = 0.0
    hmm_state: str = ""
    tf_agreement: float = 0.0
    detected_at: float = 0.0


@dataclass
class _Roll:
    mean: float
    std: float


class Detector:
    def __init__(self, *, ofi_thr: float = 0.7, vol_thr: float = 3.0,
                 shock_z: float = 2.0, cooldown: float = _EMIT_COOLDOWN) -> None:
        self._price_stats: dict[str, _Roll] = {}
        self._last_emit: dict[str, float] = {}
        self._last_dir: dict[str, str] = {}
        self.ofi_thr = ofi_thr
        self.vol_thr = vol_thr
        self.shock_z = shock_z
        self.cooldown = cooldown

    def _snapshot(self, ticker: str, f: TickerFeatures, now: float) -> Signal:
        return Signal(
            ticker=ticker, type="", category=NORMAL,
            ofi=f.ofi5m, volume_ratio=f.volume_ratio, price_change=f.price_change_1m,
            tf_agreement=f.tf_agreement, hmm_state=f.hmm_state, detected_at=now,
        )

    def classify(self, f: TickerFeatures, sigma: float) -> str:
        """detector.go::classify — panic→black_swan; ≥2 extremes→uncertain; else normal."""
        if f.hmm_state == "panic":
            return BLACK_SWAN
        score = 0
        if f.ofi5m > 0.85 or f.ofi5m < -0.85:
            score += 1
        if f.volume_ratio > 5:
            score += 1
        if sigma > 3:
            score += 1
        return UNCERTAIN if score >= 2 else NORMAL

    def check_ticker(self, ticker: str, f: TickerFeatures, now: float) -> list[Signal]:
        """Run all market triggers; return the signals that pass the cooldown."""
        out: list[Signal] = []

        def consider(sig: Signal) -> None:
            if self._allow(ticker, sig.type, now):
                out.append(sig)

        # Trigger 4: |OFI| > threshold
        if f.ofi5m > self.ofi_thr or f.ofi5m < -self.ofi_thr:
            s = self._snapshot(ticker, f, now)
            s.type = OFI_ANOMALY
            s.category = self.classify(f, 0)
            consider(s)

        # Trigger 3: volume > threshold × avg
        if f.volume_ratio > self.vol_thr:
            s = self._snapshot(ticker, f, now)
            s.type = VOL_SPIKE
            s.category = self.classify(f, 0)
            consider(s)

        # Trigger 7: cross-asset signal
        if f.cross_asset_signal in ("bullish", "bearish"):
            s = self._snapshot(ticker, f, now)
            s.type = CROSS_ASSET
            s.category = NORMAL
            consider(s)

        # Price anomaly > 2σ (EWMA of 1m PriceChangePct)
        pc = f.price_change_1m
        st = self._price_stats.get(ticker)
        if st is None:
            self._price_stats[ticker] = _Roll(mean=pc, std=0.0)
        else:
            dev = pc - st.mean
            st.mean = (1 - _PRICE_EWMA_ALPHA) * st.mean + _PRICE_EWMA_ALPHA * pc
            variance = (1 - _PRICE_EWMA_ALPHA) * (st.std ** 2) + _PRICE_EWMA_ALPHA * (dev ** 2)
            st.std = variance ** 0.5
            if st.std > 0:
                z = abs(pc - st.mean) / st.std
                if z > self.shock_z:
                    s = self._snapshot(ticker, f, now)
                    s.type = PRICE_SHOCK
                    s.sigma = z
                    s.price_change = pc
                    s.category = self.classify(f, z)
                    consider(s)

        # Higher-TF trend flip (1h flips, confirmed by 10m)
        new_dir, flipped = _trend_flip(self._last_dir.get(ticker, "flat"), f)
        if flipped:
            s = self._snapshot(ticker, f, now)
            s.type = TREND_FLIP
            s.category = self.classify(f, 0)
            consider(s)
        self._last_dir[ticker] = new_dir

        return out

    def classify_news_signal(self, ticker: str, f: TickerFeatures, severity: int, now: float) -> Signal | None:
        """News path: emit only when severity >= 6 (after classification)."""
        if severity < 6:
            return None
        if not self._allow(ticker, NEWS, now):
            return None
        s = self._snapshot(ticker, f, now)
        s.type = NEWS
        s.category = UNCERTAIN
        return s

    def _allow(self, ticker: str, etype: str, now: float) -> bool:
        key = f"{ticker}:{etype}"
        last = self._last_emit.get(key)
        if last is not None and now - last < self.cooldown:
            return False
        self._last_emit[key] = now
        return True


def _trend_flip(prev: str, f: TickerFeatures) -> tuple[str, bool]:
    """detector.go::trendFlipDirection — decisive 1h reversal confirmed by 10m."""
    cur = f.dir_1h
    cur_decisive = cur in ("long", "short")
    prev_decisive = prev in ("long", "short")
    confirmed = f.dir_10m == cur
    flipped = cur_decisive and prev_decisive and cur != prev and confirmed
    return cur, flipped
