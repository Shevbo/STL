"""TDD for the partial-reduce P&L bug: compute_metrics used to reset the entry
average to the CLOSING fill's price on any non-zero remainder, mis-realizing
every later close of an averaging strategy (macd_cross). The remaining
contracts must keep the ORIGINAL entry average on a partial reduce (same fix
already shipped in trader/lab/runtime.py and robot_runner/runtime.py).

commission_for always charges a flat BROKER_FEE_PER_CONTRACT per fill
regardless of symbol (trader/lab/commission.py) — symbol="" does NOT yield
zero commission, it just falls back to the "index" fee-group rate for the
exchange leg. So fees here are unavoidable; the expected net_profit is the
65.0 gross MINUS the exact commission computed the same way compute_metrics
computes it (not a weakened/approximate assertion)."""
import asyncio

import pytest

from trader.lab.backtest import compute_metrics, run_single_backtest
from trader.lab.commission import commission_for
from trader.lab.runtime import Bar

_SYMBOL = ""
_PV = 1.0

_TRADES = [
    {"side": "buy", "price": 100.0, "qty": 10, "time": 1000},
    {"side": "sell", "price": 110.0, "qty": 3, "time": 2000},
    {"side": "sell", "price": 105.0, "qty": 7, "time": 3000},
]


def test_commission_for_empty_symbol_is_not_zero():
    # Verifies the brief's stated assumption before relying on it: the flat
    # broker fee (0.45/contract) applies no matter what symbol string is
    # passed, so symbol="" is NOT a fees-off knob.
    assert commission_for(_SYMBOL, 100.0, 10, _PV, taker=True) > 0


def test_compute_metrics_partial_reduce_keeps_entry_avg():
    # buy 10@100, sell 3@110, sell 7@105 (gross +30 then +35 = +65 vs a flat
    # entry avg of 100). The buggy code re-based the remaining 7 contracts to
    # entry=110 after the first close, so the second close realized
    # (105-110)*7=-35 instead of (105-100)*7=+35 -> gross -5 net of fees.
    c1 = commission_for(_SYMBOL, 100.0, 10, _PV, taker=True)
    c2 = commission_for(_SYMBOL, 110.0, 3, _PV, taker=True)
    c3 = commission_for(_SYMBOL, 105.0, 7, _PV, taker=True)
    total_fee = c1 + c2 + c3

    entry_fee_closed_1 = c1 * 3 / 10
    pair1 = 30.0 - entry_fee_closed_1 - c2
    entry_fee_closed_2 = (c1 - entry_fee_closed_1) * 7 / 7
    pair2 = 35.0 - entry_fee_closed_2 - c3

    result = compute_metrics(_TRADES, initial_equity=100_000.0,
                             point_value=_PV, symbol=_SYMBOL)

    assert result["net_profit"] == pytest.approx(65.0 - total_fee)
    assert len(result["closed_pairs"]) == 2
    assert result["closed_pairs"][0]["pnl"] == pytest.approx(pair1)
    assert result["closed_pairs"][1]["pnl"] == pytest.approx(pair2)
    assert result["closed_pairs"][0]["time"] == 2000
    assert result["closed_pairs"][1]["time"] == 3000


def _bar(t, price):
    return Bar(time=t, open=price, high=price + 1, low=price - 1, close=price, volume=1)


async def on_start(rt, params):
    await rt.place_order(rt._symbol, "buy", 1, rt._bars[rt._cursor].close)


async def on_bar(rt, params):
    # Close the whole position once, well past the IS/OOS boundary, so
    # window_metrics has exactly one real closed round-trip to bucket.
    if rt._cursor == 15 and not rt.get_state("sold"):
        await rt.place_order(rt._symbol, "sell", 1, rt._bars[rt._cursor].close)
        rt.set_state("sold", True)


def test_window_metrics_wired_from_compute_metrics_closed_pairs():
    # 20 bars, price ramps 100..119 by bar open. Entry fills at bars[5].open
    # (cursor seeds to 4, on_start fills at cursor+1), exit fills at
    # bars[16].open (on_bar fires at cursor==15). span=(0, 1140), is_frac=0.7
    # -> boundary=798; exit fill_time=960 >= 798 -> the single pair lands OOS.
    bars = [_bar(i * 60, 100.0 + i) for i in range(20)]
    res = asyncio.run(run_single_backtest(
        strategy_module=__import__(__name__, fromlist=["on_bar"]),
        bars=bars, symbol="TEST", params={},
        initial_equity=100_000.0, point_value=1.0))

    assert "closed_pairs" not in res           # internal handoff, popped before return
    assert res["total_trades"] == 1
    assert res["net_profit"] != 0.0
    # Single pair, closed OOS -> all of net_profit is OOS, none IS.
    assert res["net_oos"] == pytest.approx(res["net_profit"])
