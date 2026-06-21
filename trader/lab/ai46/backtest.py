"""Container backtest support for team-46.

The live robot consumes a real trade stream for OFI. History has no ticks, so a
bar-stream simulation must SYNTHESISE order flow from each 1m bar. This module
provides the proxy: a Close-Location-Value (CLV) split of bar volume into buy
and sell, fed through the SAME OrderFlow.on_trade path so OrderFlow.ofi() over
the 300s window matches the live contract.

proxy-OFI over a window = Σ(V·CLV) / Σ V  (volume-weighted CLV = 5-bar Chaikin
Money Flow). It is an ESTIMATE of pressure from candle shape, not real order
flow: it ignores intrabar path and true aggressor side. Run it alongside an
OFI=0 baseline; the gap measures how much of the strategy rides on flow.
"""
from __future__ import annotations

import bisect

from trader.lab.ai46 import llm as LLM
from trader.lab.ai46.engine import FeatureEngine
from trader.lab.ai46.order_flow import OrderFlow
from trader.lab.ai46.runner import Ai46Runner
from trader.lab.commission import commission_for

# OrderFlow.on_trade side_enum values (marketdata proto Trade.side).
_BUY = 1
_SELL = 2


def clv(high: float, low: float, close: float) -> float:
    """Close Location Value in [-1, 1]. +1 = closed at the high (buy pressure),
    -1 = closed at the low (sell pressure), 0 = mid or zero-range bar."""
    rng = high - low
    if rng <= 0:
        return 0.0
    return ((close - low) - (high - close)) / rng


def buy_fraction(high: float, low: float, close: float) -> float:
    """Estimated fraction of bar volume that was aggressive buying, in [0, 1]."""
    return (1.0 + clv(high, low, close)) / 2.0


def feed_proxy_trades(flow: OrderFlow, symbol: str, bar, *, blend_tick: bool = False,
                      prev_close: float | None = None) -> None:
    """Synthesise two trades (buy + sell) for one bar and feed OrderFlow.

    With blend_tick the per-bar pressure mixes intrabar CLV with the inter-bar
    tick sign 0.5·CLV + 0.5·sign(C - prevC), capturing close->open gaps.
    """
    c = clv(bar.high, bar.low, bar.close)
    if blend_tick and prev_close is not None:
        tick = 1.0 if bar.close > prev_close else (-1.0 if bar.close < prev_close else 0.0)
        c = 0.5 * c + 0.5 * tick
    f = (1.0 + c) / 2.0
    v = float(bar.volume)
    t = float(bar.time)
    buy_v = v * f
    sell_v = v * (1.0 - f)
    if buy_v > 0:
        flow.on_trade(symbol, t, bar.close, buy_v, _BUY)
    if sell_v > 0:
        flow.on_trade(symbol, t, bar.close, sell_v, _SELL)


# ════════════════════════════════════════════════════════════════════════════
#  Container backtester
# ════════════════════════════════════════════════════════════════════════════

# Detector entry-signal min bars per timeframe (engine needs >= 22 to call it).
_MIN_WINDOW_BARS = 22


class Ai46Backtester:
    """Replay historical 1m bars through the real team-46 pipeline on a virtual
    clock. The runner, engine, models, detector, contrarian and risk are the
    SAME code as live; only time, bars and order flow are injected.

    ofi_mode:
      'zero'  -> no synthetic trades; OFI=0 (honest baseline, short-only).
      'proxy' -> CLV synthetic trades per bar (two-sided, approximate flow).
      'real'  -> feed real trades from ticks_by_symbol (faithful OFI).

    point_values + taker enable real FORTS commission in metrics(). clock_from/
    clock_to bound the virtual clock (e.g. one day) while bars still provide full
    lookback for the feature window.
    """

    def __init__(self, bars_by_symbol: dict, *, step_secs: int = 300,
                 window_secs: int = 3 * 86400, ofi_mode: str = "zero",
                 model_refresh_secs: float = 1800.0, model_window: int = 0,
                 model_iter: int = 40, llm_enabled: bool = False,
                 blend_tick: bool = False, ticks_by_symbol: dict | None = None,
                 point_values: dict | None = None, taker: bool = True,
                 clock_from: float | None = None, clock_to: float | None = None,
                 params=None) -> None:
        self.bars = {s: sorted(b, key=lambda x: x.time) for s, b in bars_by_symbol.items() if b}
        self.symbols = list(self.bars)
        self.times = {s: [b.time for b in self.bars[s]] for s in self.symbols}
        self.step = step_secs
        self.window = window_secs
        self.ofi_mode = ofi_mode
        self.blend_tick = blend_tick
        self.point_values = point_values or {}
        self.taker = taker
        self.clock_from = clock_from
        self.clock_to = clock_to
        self._fed = {s: 0 for s in self.symbols}        # next bar index (proxy)
        self.tick_data = {s: sorted(t, key=lambda x: x[0])
                          for s, t in (ticks_by_symbol or {}).items()}
        self._fed_tick = {s: 0 for s in self.tick_data}  # next tick index (real)
        fe = FeatureEngine(model_refresh_secs=model_refresh_secs, model_window=model_window,
                           model_iter=model_iter)
        self.runner = Ai46Runner(
            self.symbols, klod=LLM.KlodClient(enabled=llm_enabled), feature_engine=fe,
            params=params,
        )
        self.ticks = 0

    def _feed_proxy(self, sym: str, upto_t: float) -> None:
        ts, bars = self.times[sym], self.bars[sym]
        idx, n = self._fed[sym], len(bars)
        prev = bars[idx - 1].close if idx > 0 else None
        while idx < n and ts[idx] <= upto_t:
            feed_proxy_trades(self.runner.flow, sym, bars[idx],
                              blend_tick=self.blend_tick, prev_close=prev)
            prev = bars[idx].close
            idx += 1
        self._fed[sym] = idx

    def _feed_real(self, sym: str, upto_t: float) -> None:
        rows = self.tick_data.get(sym)
        if not rows:
            return
        idx, n = self._fed_tick[sym], len(rows)
        while idx < n and rows[idx][0] <= upto_t:
            t_, price, qty, side = rows[idx]
            if qty > 0:
                self.runner.flow.on_trade(sym, t_, price, qty, side)
            idx += 1
        self._fed_tick[sym] = idx

    def _feed(self, sym: str, upto_t: float) -> None:
        if self.ofi_mode == "proxy":
            self._feed_proxy(sym, upto_t)
        elif self.ofi_mode == "real":
            self._feed_real(sym, upto_t)

    async def run(self) -> dict:
        starts = [ts[0] for ts in self.times.values() if ts]
        ends = [ts[-1] for ts in self.times.values() if ts]
        if not starts:
            return self.metrics()
        t = self.clock_from if self.clock_from is not None else min(starts)
        t_end = self.clock_to if self.clock_to is not None else max(ends)
        while t <= t_end:
            bars_by: dict = {}
            for s in self.symbols:
                ts = self.times[s]
                hi = bisect.bisect_right(ts, t)
                lo = bisect.bisect_right(ts, t - self.window)
                if hi - lo >= _MIN_WINDOW_BARS:
                    bars_by[s] = self.bars[s][lo:hi]
                self._feed(s, t)
            if bars_by:
                await self.runner.tick(t, bars_by)
                self.ticks += 1
            t += self.step
        return self.metrics()

    def _fee_frac(self, sym: str, price: float, pv: float) -> float:
        """Per-side commission as a fraction of notional (= rate + broker/notional)."""
        notional = abs(price) * (pv or 1.0)
        if notional <= 0:
            return 0.0
        return commission_for(sym, price, 1, pv, taker=self.taker) / notional

    def metrics(self) -> dict:
        fills = self.runner.exec.fills
        per: dict = {}
        equity: list = []
        cum = gross = fees = 0.0
        open_px: dict = {}   # sym -> (price, size_pct) of the matching open leg
        for f in fills:
            d = per.setdefault(f.ticker, {"opens": 0, "closes": 0, "pnl": 0.0,
                                          "fees": 0.0, "net": 0.0, "wins": 0,
                                          "shorts": 0, "longs": 0})
            if f.kind == "open":
                d["opens"] += 1
                d["shorts" if f.side == "sell" else "longs"] += 1
                open_px[f.ticker] = (f.price, f.size_pct)
            else:
                d["closes"] += 1
                d["pnl"] += f.pnl
                gross += f.pnl
                if f.pnl > 0:
                    d["wins"] += 1
                pv = self.point_values.get(f.ticker, 1.0)
                fee = self._fee_frac(f.ticker, f.price, pv) * f.size_pct  # close side
                op = open_px.pop(f.ticker, None)
                if op is not None:
                    fee += self._fee_frac(f.ticker, op[0], pv) * op[1]    # open side
                d["fees"] += fee
                fees += fee
                cum += f.pnl - fee
                equity.append(cum)
        peak = max_dd = 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, peak - e)
        closes = sum(d["closes"] for d in per.values())
        wins = sum(d["wins"] for d in per.values())
        for d in per.values():
            d["net"] = round(d["pnl"] - d["fees"], 6)
            d["pnl"] = round(d["pnl"], 6)
            d["fees"] = round(d["fees"], 6)
        return {
            "ofi_mode": self.ofi_mode,
            "ticks": self.ticks,
            "symbols": len(self.symbols),
            "gross_pnl": round(gross, 6),
            "fees": round(fees, 6),
            "net_pnl": round(cum, 6),
            "total_pnl": round(gross, 6),        # back-compat alias (gross)
            "trades_closed": closes,
            "win_rate": round(wins / closes, 4) if closes else 0.0,
            "max_drawdown": round(max_dd, 6),    # on NET equity
            "n_fills": len(fills),
            "per_symbol": per,
        }
