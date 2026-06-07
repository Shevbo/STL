"""Local validation of Shectory-2EMA: fetch bars from ISS, run the backtest, print
metrics. Step 1: 3-month single contract (RIM6). Step 2: 1-year continuous RTS (RI).
Usage: python scripts/diag_2ema.py RIM6 2026-03-07 2026-06-06
       python scripts/diag_2ema.py RI   2025-04-16 2026-03-29
"""
import asyncio
import sys
import types
from datetime import date

from trader.lab.iss_loader import fetch_contract_spec, load_bars_iss
from trader.lab.backtest import run_single_backtest
from trader.lab.strategies.library import make_on_bar


def _d(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


async def main() -> None:
    sym = sys.argv[1] if len(sys.argv) > 1 else "RIM6"
    d_from = _d(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 3, 7)
    d_to = _d(sys.argv[3]) if len(sys.argv) > 3 else date(2026, 6, 6)
    print(f"loading {sym} {d_from}..{d_to} (1m)…", flush=True)
    bars = await load_bars_iss(sym, d_from, d_to, 1)
    print(f"bars: {len(bars)}", flush=True)
    if not bars:
        print("no bars")
        return
    # ruble economics (use front contract for a base code)
    spec_sym = sym if sym[-1:].isdigit() else "RIM6"
    spec = await fetch_contract_spec(spec_sym) or {}
    pv = spec.get("point_value") or 1.0
    im = spec.get("initial_margin") or 0.0
    print(f"point_value={pv} initial_margin={im}", flush=True)

    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("shectory_2ema")
    params = {"symbol": sym, "ema1": 10, "ema2": 140, "qty": 1, "bet_step": 1, "bet_max": 10}
    r = await run_single_backtest(mod, bars, sym, params, point_value=pv, initial_margin=im)
    keys = ("total_trades", "win_rate", "net_profit", "total_return",
            "recovery_factor", "sharpe", "max_drawdown", "peak_contracts", "margin_used")
    print("=== Shectory-2EMA (EMA1=10, EMA2=140, bet_step=1) ===")
    for k in keys:
        print(f"  {k}: {r.get(k)}")


if __name__ == "__main__":
    asyncio.run(main())
