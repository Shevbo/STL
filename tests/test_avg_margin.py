"""Averaging-to-N modifier + real-ГО return metric."""
import asyncio

from trader.lab.backtest import compute_metrics, run_single_backtest
from trader.lab.runtime import Bar
from trader.lab.strategies import library as L


def test_return_uses_real_margin_not_flat_100k():
    # open 1@100, add 1@90, add 1@80 (peak 3 contracts), close 3@100
    trades = [
        {"side": "buy", "price": 100, "qty": 1, "time": 1},
        {"side": "buy", "price": 90, "qty": 1, "time": 2},
        {"side": "buy", "price": 80, "qty": 1, "time": 3},
        {"side": "sell", "price": 100, "qty": 3, "time": 4},
    ]
    m = compute_metrics(trades, initial_equity=100_000, point_value=1.0,
                        symbol="RIM6", initial_margin=20_000)
    assert m["peak_contracts"] == 3
    assert m["margin_used"] == 60_000                      # 3 × ГО, not 100k
    assert abs(m["total_return"] - m["net_profit"] / 60_000) < 1e-9


def test_return_falls_back_to_equity_when_margin_unknown():
    trades = [
        {"side": "buy", "price": 100, "qty": 1, "time": 1},
        {"side": "sell", "price": 110, "qty": 1, "time": 2},
    ]
    m = compute_metrics(trades, initial_equity=100_000, point_value=1.0,
                        symbol="RIM6", initial_margin=0.0)
    assert m["peak_contracts"] == 1
    assert m["margin_used"] == 100_000
    assert abs(m["total_return"] - m["net_profit"] / 100_000) < 1e-9


def _bars(prices):
    return [Bar(time=i * 60, open=p, high=p + 0.5, low=p - 0.5, close=p, volume=1)
            for i, p in enumerate(prices)]


def test_averaging_adds_contracts_on_adverse_move():
    L.register("t_long_only", "T", "x",
               [L.SYM, L.P("qty", "q", 1, 1, 10)], lambda bars, p: 1, lambda p: 2)
    on_bar = L.make_on_bar("t_long_only")
    # steady steep decline → averaging должен добирать (шаг 1×ATR, ATR≈1)
    bars = _bars([100, 97, 94, 91, 88, 85, 82, 79, 76, 73, 70])
    rt = type("RT", (), {})()  # placeholder; use the real runtime below
    from trader.lab.runtime import BacktestRuntime
    rt = BacktestRuntime(bars=bars, symbol="RIM6", initial_equity=100_000, point_value=1.0)
    params = {"symbol": "RIM6", "qty": 1, "avg_max": 4,
              "avg_step_atr": 10, "tp_atr": 0, "avg_atr_n": 3}

    async def run():
        while True:
            await on_bar(rt, params)
            if not rt.advance():
                break
    asyncio.run(run())
    orders = asyncio.run(rt.get_orders())
    buys = sum(o.qty for o in orders if o.side == "buy")
    assert buys >= 2                       # base entry + at least one average-in
    pos = asyncio.run(rt.get_position("RIM6"))
    assert pos.quantity <= 4               # never exceeds avg_max


def test_betting_system_grows_after_loss_and_off_when_disabled():
    from trader.lab.runtime import BacktestRuntime
    # Controlled signal (long while close>=100, else short, warmup=1) on a path that
    # forces late entries → losing flips, so the betting size must grow above base.
    L.register("t_thresh", "Tx", "x",
               [L.SYM, L.P("qty", "q", 1, 1, 20), L.P("bet_step", "b", 1, 0, 5),
                L.P("bet_max", "m", 10, 1, 30)],
               lambda bars, p: 1 if bars[-1].close >= 100 else -1, lambda p: 1)
    on_bar = L.make_on_bar("t_thresh")
    prices = [100, 100, 100, 100, 100, 110, 90, 90, 110, 110, 90, 90, 110, 110]

    def run(bet):
        rt = BacktestRuntime(bars=_bars(prices), symbol="RIM6", initial_equity=50_000_000, point_value=1.0)
        params = {"symbol": "RIM6", "qty": 1, "bet_step": bet, "bet_max": 10}

        async def go():
            while True:
                await on_bar(rt, params)
                if not rt.advance():
                    break
        asyncio.run(go())
        return asyncio.run(rt.get_orders())

    on_orders = run(1)
    off_orders = run(0)
    assert max((o.qty for o in on_orders), default=0) >= 2   # betting added size after losses
    assert max((o.qty for o in off_orders), default=0) == 1  # disabled → always base size


def test_2ema_registered_with_betting():
    r = L.REGISTRY["shectory_2ema"]
    assert r["name"] == "Shectory-2EMA"
    keys = {p["key"] for p in r["params_schema"]}
    assert {"ema1", "ema2", "bet_step", "bet_max"} <= keys


def test_avg_off_by_default_behaves_like_plain_flip():
    L.register("t_flip", "T", "x",
               [L.SYM, L.P("qty", "q", 1, 1, 10)], lambda bars, p: 1, lambda p: 2)
    on_bar = L.make_on_bar("t_flip")
    from trader.lab.runtime import BacktestRuntime
    bars = _bars([100, 99, 98, 97, 96, 95, 94, 93])
    rt = BacktestRuntime(bars=bars, symbol="RIM6", initial_equity=100_000, point_value=1.0)
    params = {"symbol": "RIM6", "qty": 1}   # no avg params → defaults off

    async def run():
        while True:
            await on_bar(rt, params)
            if not rt.advance():
                break
    asyncio.run(run())
    pos = asyncio.run(rt.get_position("RIM6"))
    assert pos.quantity == 1                # only the single base contract, no averaging
