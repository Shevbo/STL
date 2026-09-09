"""Атомарный прогон дня инцидента 09.09.2026 (ложный разворот macd_shectory1 наверху).

Живые конфиги agent-macdshort и lxk22, ось flip_min_pts. Печатает по каждому:
итоговый net за окно, max_mae, и ВСЕ сделки за 09.09 с временем — видно, гасит
ли порог кроссовер ~11:31 и что стало с P&L дня.

ЗАПУСК НА ХОСТЕРЕ (ISS с dev-бокса недоступна):
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/atomic_flip_0909.py
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from trader.lab.strategies import library
from trader.lab.backtest import run_single_backtest
from trader.lab.iss_loader import load_bars_iss, fetch_contract_spec

SYMBOL = "RIU6"
D_FROM, D_TO = date(2026, 9, 1), date(2026, 9, 9)
INCIDENT_DAY = "2026-09-09"
FLIP_PTS = [0, 1400, 1750, 2100, 3000]

BASE_COMMON = dict(
    qty=1, fast=57, slow=48, signal=10, avg_atr_n=25, avg_step_atr=21,
    min_gap_pts=0, cooldown_min=0, cooldown_pct=1, nd_days=5, gap_auto=0,
    sl_frac=0, sl_pct=100, dv_bars=60, dv_range_pts=300,
    tod_m1=600, tod_m2=1080, tod_s1=3, tod_s2=2, tod_s3=1, bar_offset_min=0,
    symbol=SYMBOL,
)
CONFIGS = {
    "macdshort": dict(BASE_COMMON, avg_max=10, tp_atr=40, k_avg=10,
                      allow_long=0, allow_short=1),
    "lxk22": dict(BASE_COMMON, avg_max=20, tp_atr=80, k_avg=20,
                  allow_long=1, allow_short=1,
                  bet_step=2, bet_max=10, super_y=2, super_z=2),
}


class _Mod:
    on_bar = staticmethod(library.make_on_bar("macd_shectory1"))


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")


async def main() -> None:
    bars = await load_bars_iss(SYMBOL, D_FROM, D_TO, interval=1)
    spec = await fetch_contract_spec(SYMBOL) or {}
    pv = float(spec.get("point_value") or 1.0)
    im = float(spec.get("initial_margin") or 0.0)
    print(f"{SYMBOL} {D_FROM}..{D_TO}  бары={len(bars)}  ₽/пункт={pv:.3f}  ГО={im:,.0f}\n")

    for name, base in CONFIGS.items():
        print(f"═══ {name} ═══")
        print(f"{'flip':>5} {'net,₽':>12} {'max_mae,₽':>11} {'сделок':>7} "
              f"{'сделок 09.09':>12}  первая/последняя 09.09")
        for fmp in FLIP_PTS:
            params = dict(base, flip_min_pts=fmp)
            res = await run_single_backtest(_Mod, bars, SYMBOL, params,
                                            point_value=pv, initial_margin=im)
            trs = res.get("trades", [])
            day = [t for t in trs if _hhmm(t["time"]).startswith("09-09")]
            span = f"{_hhmm(day[0]['time'])[6:]}..{_hhmm(day[-1]['time'])[6:]}" if day else "-"
            print(f"{fmp:>5} {res['net_profit']:>12,.0f} {res.get('max_mae', 0):>11,.0f} "
                  f"{len(trs):>7} {len(day):>12}  {span}")
        # Подробные сделки 09.09 для крайних порогов.
        for fmp in (0, 1750):
            params = dict(base, flip_min_pts=fmp)
            res = await run_single_backtest(_Mod, bars, SYMBOL, params,
                                            point_value=pv, initial_margin=im)
            day = [t for t in res.get("trades", []) if _hhmm(t["time"]).startswith("09-09")]
            print(f"\n  -- {name} flip_min_pts={fmp}: сделки 09.09 --")
            for t in day:
                print(f"     {_hhmm(t['time'])}  {t['side']:4} {t['qty']:>3} @ {t['price']:.0f}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
