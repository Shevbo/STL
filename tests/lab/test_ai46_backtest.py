"""Proxy-OFI (CLV) tests for the team-46 container backtest."""
import asyncio
import math
from dataclasses import dataclass

from trader.lab.ai46 import backtest as BT
from trader.lab.ai46.order_flow import OrderFlow


@dataclass
class _Bar:
    time: float
    open: float
    high: float
    low: float
    close: float
    volume: float


def test_clv_extremes_and_mid():
    assert BT.clv(2.0, 0.0, 2.0) == 1.0   # close at high
    assert BT.clv(2.0, 0.0, 0.0) == -1.0  # close at low
    assert BT.clv(2.0, 0.0, 1.0) == 0.0   # close mid
    assert BT.clv(5.0, 5.0, 5.0) == 0.0   # zero range


def test_buy_fraction():
    assert BT.buy_fraction(2.0, 0.0, 2.0) == 1.0
    assert BT.buy_fraction(2.0, 0.0, 0.0) == 0.0
    assert BT.buy_fraction(2.0, 0.0, 1.0) == 0.5


def test_single_bar_proxy_ofi_equals_clv():
    # For a single bar, OFI over the window = (buy-sell)/(buy+sell) = CLV.
    for close, expected in [(2.0, 1.0), (0.0, -1.0), (1.0, 0.0), (1.5, 0.5)]:
        flow = OrderFlow()
        bar = _Bar(time=0.0, open=1.0, high=2.0, low=0.0, close=close, volume=100.0)
        BT.feed_proxy_trades(flow, "X", bar)
        assert abs(flow.ofi("X", 300) - expected) < 1e-9


def test_window_is_volume_weighted_clv():
    # Two bars in the 300s window: a buy-heavy big bar and a sell-heavy small bar.
    flow = OrderFlow()
    b1 = _Bar(time=0.0, open=1, high=2, low=0, close=2.0, volume=300.0)   # CLV +1
    b2 = _Bar(time=60.0, open=1, high=2, low=0, close=0.0, volume=100.0)  # CLV -1
    BT.feed_proxy_trades(flow, "X", b1)
    BT.feed_proxy_trades(flow, "X", b2)
    # Σ(V·CLV)/ΣV = (300·1 + 100·(-1)) / 400 = 0.5
    assert abs(flow.ofi("X", 300) - 0.5) < 1e-9


def test_blend_tick_uses_gap_direction():
    # Flat candle (CLV 0) but price gapped up vs prev close -> positive pressure.
    flow = OrderFlow()
    bar = _Bar(time=0.0, open=1.0, high=1.0, low=1.0, close=1.0, volume=100.0)
    BT.feed_proxy_trades(flow, "X", bar, blend_tick=True, prev_close=0.5)
    # c = 0.5*0 + 0.5*sign(1-0.5)= +0.5 -> ofi 0.5
    assert abs(flow.ofi("X", 300) - 0.5) < 1e-9


def _make_bars(n: int, t0: int = 0, base: float = 100.0):
    """Deterministic synthetic 1m bars: drift + oscillation + periodic vol spike."""
    bars = []
    for i in range(n):
        drift = i * 0.01
        c = base + drift + math.sin(i / 7.0) * 0.6
        o = base + drift + math.sin((i - 1) / 7.0) * 0.6
        hi = max(o, c) + 0.25
        lo = min(o, c) - 0.25
        v = 100.0 + (350.0 if i % 40 == 0 else 0.0)  # periodic volume spike
        bars.append(_Bar(time=t0 + i * 60, open=o, high=hi, low=lo, close=c, volume=v))
    return bars


def test_backtester_runs_zero_and_proxy():
    src = {"AAA": _make_bars(400), "BBB": _make_bars(400, base=50.0)}
    for mode in ("zero", "proxy"):
        bt = BT.Ai46Backtester(
            {k: list(v) for k, v in src.items()},
            step_secs=300, window_secs=10 * 86400,
            ofi_mode=mode, model_refresh_secs=3600, llm_enabled=False,
        )
        m = asyncio.run(bt.run())
        assert m["ticks"] > 0
        assert m["ofi_mode"] == mode
        assert m["symbols"] == 2
        for key in ("total_pnl", "trades_closed", "win_rate", "max_drawdown", "per_symbol"):
            assert key in m
