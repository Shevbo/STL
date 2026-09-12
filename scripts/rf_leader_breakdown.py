"""Поминутный разбор лидера rich_fool: воспроизводимость, дневной P&L, разбор
самых крупных дней филл за филлом.

Отвечает на вопрос «откуда взялся итог»: из преимущества или из размера позиции
на нескольких днях. Одновременно сверяет пересчёт с числом из лидерборда —
строка лидерборда старше последней правки кода это история, а не оценка.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_leader_breakdown.py [--cmp]
  --cmp  дополнительно считает тот же конфиг при плоском объёме (vol_mult=10)
         и зеркало (invert=1) — цена пирамиды и цена направления.
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import json
import sys

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

D_FROM, D_TO = "2026-03-09", "2026-09-09"

# Лидер ночного прогона rf2fadeRI (net 2 214 471 / 31 сделка / MAE 161 629)
LEADER = dict(qty=1, symbol="RI", open_hour=10, open_min=0, place_lead_min=10,
              hold_min=30, n_days=3, step_count=5, vol_mult=30,
              sl_price_pct=220, trail_tp_pct=120, max_contracts=120,
              invert=0, allow_long=1, allow_short=1, bar_offset_min=0)


def load(symbol: str) -> list[Bar]:
    rows = json.load(open(f"agent_bars/{symbol}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(D_FROM).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(D_TO).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows if lo <= r[0] <= hi]


def ts(t) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(t or 0, datetime.timezone.utc)


def round_trips(trades: list[dict]) -> list[dict]:
    """Круги: от флэта до флэта. Возвращает вход/выход/объём/пик/результат в пунктах."""
    out, pos, avg, opened, fills = [], 0, 0.0, None, []
    peak = 0
    for t in sorted(trades, key=lambda x: x["time"] or 0):
        q = t["qty"] * (1 if t["side"] == "buy" else -1)
        if pos == 0:
            opened, fills, avg, peak = t["time"], [], t["price"], abs(q)
        fills.append(t)
        if pos == 0 or (pos > 0) == (q > 0):          # открытие или добор
            tot = abs(pos) + abs(q)
            avg = (avg * abs(pos) + t["price"] * abs(q)) / tot if pos else t["price"]
            pos += q
            peak = max(peak, abs(pos))
        else:                                          # закрытие
            closed = min(abs(pos), abs(q))
            gross = (t["price"] - avg) * (1 if pos > 0 else -1) * closed
            pos += q
            if pos == 0:
                out.append({"open": opened, "close": t["time"], "side": "SHORT" if gross and avg else "",
                            "dir": "SHORT" if fills[0]["side"] == "sell" else "LONG",
                            "avg": avg, "exit": t["price"], "peak": peak,
                            "pts": gross, "fills": list(fills)})
                fills = []
    if pos != 0 and opened is not None:
        out.append({"open": opened, "close": None, "dir": "SHORT" if fills[0]["side"] == "sell" else "LONG",
                    "avg": avg, "exit": None, "peak": peak, "pts": 0.0, "fills": list(fills)})
    return out


async def run(mod, bars, over=None):
    p = {**LEADER, **(over or {})}
    return await run_single_backtest(mod, bars, p["symbol"], p, point_value=1.0)


async def main() -> None:
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    bars = load("RI")
    res = await run(mod, bars)
    print(f"баров {len(bars)}  {ts(bars[0].time):%Y-%m-%d} .. {ts(bars[-1].time):%Y-%m-%d}")
    print(f"ПЕРЕСЧЁТ ЛИДЕРА: net={res['net_profit']:,.0f}  сделок={res['total_trades']}  "
          f"win={res['win_rate']:.2f}  MAE={res['max_mae']:,.0f}  пик_контрактов={res['peak_contracts']}")
    print("в лидерборде:    net=2,214,471  сделок=31  win=0.65  MAE=161,629")

    rts = round_trips(res["trades"])
    print(f"\n=== КРУГИ (от флэта до флэта): {len(rts)} ===")
    print(f"{'открыт':<17}{'закрыт':<17}{'напр':<6}{'пик':>5}{'средняя':>11}{'выход':>10}{'пункты':>12}")
    for r in rts:
        cl = f"{ts(r['close']):%Y-%m-%d %H:%M}" if r["close"] else "ОТКРЫТ"
        ex = f"{r['exit']:,.0f}" if r["exit"] else "-"
        print(f"{ts(r['open']):%Y-%m-%d %H:%M}  {cl:<17}{r['dir']:<6}{r['peak']:>5}"
              f"{r['avg']:>11,.0f}{ex:>10}{r['pts'] * r['peak']:>12,.0f}")

    # вклад дней: сколько кругов делают итог
    tot = sum(r["pts"] * r["peak"] for r in rts)
    best = sorted(rts, key=lambda r: -abs(r["pts"] * r["peak"]))[:3]
    print(f"\nсумма по кругам (пункты×пик) = {tot:,.0f}")
    share = sum(r["pts"] * r["peak"] for r in best)
    print(f"три крупнейших круга дают {share:,.0f} = {100 * share / tot if tot else 0:.0f}% итога")

    print("\n=== ПОМИНУТНО: три крупнейших круга ===")
    bmap = {b.time: b for b in bars}
    for r in best:
        print(f"\n--- {r['dir']}, открыт {ts(r['open']):%Y-%m-%d %H:%M}, "
              f"пик {r['peak']} контр., итог {r['pts'] * r['peak']:,.0f} пунктов ---")
        print(f"{'время':<17}{'действие':<8}{'кол':>4}{'цена':>10}{'поз':>6}   бар O/H/L/C")
        pos = 0
        for f in r["fills"]:
            pos += f["qty"] * (1 if f["side"] == "buy" else -1)
            b = bmap.get(f["time"])
            bar = f"{b.open:,.0f}/{b.high:,.0f}/{b.low:,.0f}/{b.close:,.0f}" if b else "-"
            act = "ПОКУПКА" if f["side"] == "buy" else "ПРОДАЖА"
            print(f"{ts(f['time']):%Y-%m-%d %H:%M}  {act:<8}{f['qty']:>4}{f['price']:>10,.0f}{pos:>6}   {bar}")

    if "--cmp" in sys.argv:
        print("\n=== ЦЕНА ПИРАМИДЫ И ЦЕНА НАПРАВЛЕНИЯ (тот же конфиг) ===")
        for label, over in [("плоский объём vol_mult=10", dict(vol_mult=10)),
                            ("зеркало invert=1 (пробой)", dict(invert=1)),
                            ("одна ступень step_count=1", dict(step_count=1))]:
            r = await run(mod, bars, over)
            print(f"  {label:<30} net={r['net_profit']:>12,.0f} сделок={r['total_trades']:>4} "
                  f"MAE={r['max_mae']:>11,.0f} пик={r['peak_contracts']:>4} "
                  f"net/MAE={(r['net_profit'] / r['max_mae']) if r['max_mae'] else 0:>6.1f}")


if __name__ == "__main__":
    asyncio.run(main())
