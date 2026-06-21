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
class _Bundle:
    """Slow-moving features cached on the refresh interval (per symbol)."""
    t: float
    directions: dict
    hmm_state: str
    garch_vol: float
    vwap: float


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
    """bars_1m + order flow -> MarketFeatures.

    model_refresh_secs > 0 caches the HMM regime + GARCH vol per symbol and only
    re-fits them when that many seconds of data-time have passed. Live uses 0
    (re-fit every tick). A backtest replaying months of history sets it (e.g.
    1800s) so Baum-Welch / Nelder-Mead don't run on every step — the dominant
    cost. Cheap features (directions, OFI, VWAP, price change) stay per-tick.
    """

    def __init__(self, model_refresh_secs: float = 0.0, model_window: int = 0,
                 model_iter: int = 40) -> None:
        self._refresh = model_refresh_secs
        self._model_window = model_window          # 0 = all returns (live); >0 cap for backtest
        self._model_iter = model_iter              # HMM Baum-Welch iterations (live 40)
        self._model_cache: dict[str, _Bundle] = {}

    def _bundle(self, symbol: str, bars_1m: list, data_time: float) -> _Bundle:
        """Per-TF directions, HMM regime, GARCH vol, VWAP. Cached on the refresh
        interval; recomputed every call when _refresh == 0 (live behaviour)."""
        if self._refresh > 0:
            c = self._model_cache.get(symbol)
            if c is not None and (data_time - c.t) < self._refresh:
                return c
        closes = [b.close for b in bars_1m]
        directions: dict[str, str] = {}
        for tf in _FRAMES:
            h, lo, cc, _v = _aggregate(bars_1m, _TF_SECONDS[tf])
            directions[tf] = _direction(h, lo, cc) if len(cc) >= 22 else "flat"
        rets = MOD._log_returns(closes)
        m_rets = rets[-self._model_window:] if self._model_window else rets
        hmm = MOD.hmm_regime(m_rets, n_iter=self._model_iter)
        garch = MOD.garch11_forecast(m_rets)
        highs = [b.high for b in bars_1m]
        lows = [b.low for b in bars_1m]
        vols = [float(b.volume) for b in bars_1m]
        bundle = _Bundle(
            t=data_time, directions=directions,
            hmm_state=hmm.state if hmm else "flat",
            garch_vol=garch.forecast_vol if garch else 0.0,
            vwap=F.vwap(highs, lows, closes, vols),
        )
        self._model_cache[symbol] = bundle
        return bundle

    def compute(self, symbol: str, bars_1m: list, order_flow=None) -> MarketFeatures | None:
        if not bars_1m or len(bars_1m) < 2:
            return None
        b = self._bundle(symbol, bars_1m, bars_1m[-1].time)
        # Cheap, fast-moving fields recomputed every call (O(1) / O(period)), so a
        # throttled bundle does not stale the price-shock / vol-spike / OFI triggers.
        last, prev = bars_1m[-1], bars_1m[-2]
        price_change_1m = ((last.close - prev.close) / prev.close * 100.0) if prev.close else 0.0
        vol_tail = [float(x.volume) for x in bars_1m[-21:]]   # volume_ratio uses last period+1
        ofi5m = order_flow.ofi(symbol, 300) if order_flow is not None else 0.0

        mf = MarketFeatures(
            ofi5m=ofi5m,
            volume_ratio=F.volume_ratio(vol_tail, 20),
            price_change_1m=price_change_1m,
            hmm_state=b.hmm_state,
            cross_asset_signal="",                      # multi-asset: out of scope
            dir_1h=b.directions["1h"],
            dir_10m=b.directions["10m"],
            directions=b.directions,
            garch_vol=b.garch_vol,
            vwap=b.vwap,
        )
        # tf_agreement in the OFI-inferred direction (engine.go snapshotSignal).
        infer = "short" if mf.ofi5m < -0.1 else "long"
        mf.tf_agreement = mf.agreement_ratio(infer)
        return mf
