"""Общие помощники для проверки райдера sl_rev: синтетические ряды и прогон make_on_bar."""
import asyncio
import random

from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
DAY0 = 1788307200          # среда 2026-09-02 00:00


def walk(seed: int, n: int = 2600, start_min: int = 600, step: float = 10.0, vol: float = 25.0, day_gap: int = 0):
    """Случайное блуждание с трендовыми кусками, минутные бары; day_gap>0 - разрыв в сутках после 1300 баров."""
    r = random.Random(seed)
    px, drift, out = 100000.0, 0.0, []
    for i in range(n):
        if i % 180 == 0:
            drift = r.uniform(-6, 6)
        o = px
        c = px + drift + r.gauss(0, vol)
        c = round(c / step) * step
        hi = max(o, c) + abs(r.gauss(0, vol / 3))
        lo = min(o, c) - abs(r.gauss(0, vol / 3))
        t = DAY0 + (start_min + i + (1440 * day_gap if (day_gap and i >= 1300) else 0)) * 60
        out.append(Bar(time=t, open=o, high=round(hi / step) * step, low=round(lo / step) * step, close=c, volume=1))
        px = c
    return out


def run(rid: str, bars, params: dict):
    """-> список (side, qty, price, fill_time) всех заявок."""
    async def go():
        rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
        on_bar = make_on_bar(rid)
        p = {"symbol": SYM, **params}
        while True:
            await on_bar(rt, p)
            if not rt.advance():
                break
        return rt
    rt = asyncio.run(go())
    return [(o.side, int(o.qty), float(o.fill_price), int(o.fill_time)) for o in rt._orders], rt


CASES = {
    "2ema_sl": ("shectory_2ema", dict(ema1=5, ema2=20, qty=1, sl_pct=30, bet_step=1, bet_max=3, avg_max=3, avg_step_atr=14, tp_atr=60)),
    "2ema_plain": ("shectory_2ema", dict(ema1=8, ema2=40, qty=1, sl_pct=50)),
    "macd_sl": ("macd_shectory1", dict(fast=12, slow=26, signal=9, qty=1, sl_pct=50, avg_max=4, k_avg=15, avg_step_atr=15, tp_atr=40, bet_step=1, bet_max=5)),
    "macd_nosl": ("macd_shectory1", dict(fast=10, slow=30, signal=9, qty=1, avg_max=3, avg_step_atr=20, tp_atr=50)),
}
SEEDS = (1, 2, 3)
