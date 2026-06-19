"""FeatureEngine for team-46 — assembles Phase 1/2/3 into the per-symbol
features the detector and contrarian session consume.

Port of go-bot/internal/features/engine.go ComputeTicker: per-timeframe
direction (ema9 vs ema21 + rsi14), 4-frame AgreementRatio, OFI5m, VolumeRatio,
VWAP, HMM regime and GARCH vol. Shectory feeds 1m bars (ISS); higher timeframes
are aggregated from them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trader.lab.ai46 import features as F
from trader.lab.ai46 import models as MOD
from trader.lab.ai46 import detector as DET

_FRAMES = ["1d", "1h", "10m", "1m"]
_TF_SECONDS = {"1m": 60, "10m": 600, "1h": 3600, "1d": 86400}


@dataclass
class MarketFeatures(DET.TickerFeatures):
    """detector.TickerFeatures + the extra fields/methods contrarian needs."""
    directions: dict = field(default_factory=dict)   # tf -> "long"|"short"|"flat"
    garch_vol: float = 0.0
    vwap: float = 0.0

    def agreement_ratio(self, direction: str) -> float:
        """engine.go::AgreementRatio — fraction of [1d,1h,10m,1m] aligned."""
        n = sum(1 for tf in _FRAMES if self.directions.get(tf) == direction)
        return n / len(_FRAMES)


def _aggregate(bars: list, tf_secs: int) -> tuple[list, list, list, list]:
    """Aggregate 1m bars (objects with time/open/high/low/close/volume) into
    tf_secs OHLCV buckets. Returns (highs, lows, closes, volumes)."""
    if tf_secs <= 60:
        return ([b.high for b in bars], [b.low for b in bars],
                [b.close for b in bars], [float(b.volume) for b in bars])
    buckets: dict[int, list] = {}
    order: list[int] = []
    for b in bars:
        k = int(b.time) // tf_secs
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(b)
    highs, lows, closes, vols = [], [], [], []
    for k in order:
        grp = buckets[k]
        highs.append(max(x.high for x in grp))
        lows.append(min(x.low for x in grp))
        closes.append(grp[-1].close)
        vols.append(sum(float(x.volume) for x in grp))
    return highs, lows, closes, vols


def _direction(highs, lows, closes) -> str:
    """engine.go per-TF: long if ema9>ema21 & rsi<70; short if ema9<ema21 & rsi>30."""
    e9 = F.ema(closes, 9)
    e21 = F.ema(closes, 21)
    if e9 is None or e21 is None:
        return "flat"
    a, b = F.last(e9), F.last(e21)
    r = F.rsi(closes, 14)
    if a > b and r < 70:
        return "long"
    if a < b and r > 30:
        return "short"
    return "flat"


class FeatureEngine:
    """Stateless compute: bars_1m + order flow -> MarketFeatures."""

    def compute(self, symbol: str, bars_1m: list, order_flow=None) -> MarketFeatures | None:
        if not bars_1m or len(bars_1m) < 2:
            return None
        closes_1m = [b.close for b in bars_1m]
        vols_1m = [float(b.volume) for b in bars_1m]
        highs_1m = [b.high for b in bars_1m]
        lows_1m = [b.low for b in bars_1m]

        directions: dict[str, str] = {}
        for tf in _FRAMES:
            h, lo, c, v = _aggregate(bars_1m, _TF_SECONDS[tf])
            directions[tf] = _direction(h, lo, c) if len(c) >= 22 else "flat"

        rets = MOD._log_returns(closes_1m)
        hmm = MOD.hmm_regime(rets)
        garch = MOD.garch11_forecast(rets)
        prev = closes_1m[-2]
        price_change_1m = ((closes_1m[-1] - prev) / prev * 100.0) if prev else 0.0

        ofi5m = order_flow.ofi(symbol, 300) if order_flow is not None else 0.0

        mf = MarketFeatures(
            ofi5m=ofi5m,
            volume_ratio=F.volume_ratio(vols_1m, 20),
            price_change_1m=price_change_1m,
            hmm_state=hmm.state if hmm else "flat",
            cross_asset_signal="",                      # multi-asset: out of scope
            dir_1h=directions["1h"],
            dir_10m=directions["10m"],
            directions=directions,
            garch_vol=garch.forecast_vol if garch else 0.0,
            vwap=F.vwap(highs_1m, lows_1m, closes_1m, vols_1m),
        )
        # tf_agreement in the OFI-inferred direction (engine.go snapshotSignal).
        infer = "short" if mf.ofi5m < -0.1 else "long"
        mf.tf_agreement = mf.agreement_ratio(infer)
        return mf
