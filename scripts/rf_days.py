"""Хит-парад дней лидера rich_fool: прибыльные, засадные, поминутный разбор.

Отвечает на вопрос «откуда взялся итог»: из ровной работы механики или из пары
дней. Если три дня делают 80% результата — это не стратегия, а лотерея, и никакие
net/MAE этого не покажут.

Даёт четыре блока:
  1. сводка: дней торговали, плюсовых/минусовых, вклад топ-3 в итог;
  2. ХИТ-ПАРАД прибыльных дней и ЗАСАДНЫХ, с пиком позиции и причиной выхода;
  3. поминутный разбор самых крупных дней обеих сторон — каждый филл с баром;
  4. распределение по дням недели (выходные FORTS торгует отдельной сессией).

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_days.py [SECID] [--top N]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import importlib
import json

from trader.lab.backtest import run_single_backtest
from trader.lab.runtime import Bar

WINDOWS = {
    "RIM6": ("2026-03-20", "2026-06-17"),
    "RIU6": ("2026-06-19", "2026-09-09"),
    "SiM6": ("2026-03-20", "2026-06-17"),
    "SiU6": ("2026-06-19", "2026-09-09"),
}

# Лидер волны 2, пересчитываемый с ЧЕСТНОЙ наливкой: защита 50 пунктов.
LEADER = dict(qty=1, d_coef=5, hold_min=20, n_days=5, step_count=8, vol_mult=25,
              sl_price_pct=50, trail_tp_pct=10, max_contracts=60,
              slip_guard_pts=50, slip_pct=0,
              place_lead_min=10, ema_fast=9, ema_slow=21, exit_lead_min=120,
              invert=0, allow_long=1, allow_short=1, bar_offset_min=0)

RU_WD = ["пн", "вт", "ср", "чт", "пт", "СБ", "ВС"]


def load(secid: str) -> list[Bar]:
    a, b = WINDOWS[secid]
    rows = json.load(open(f"agent_bars/{secid}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(a).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(b).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    return [Bar(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows if lo <= r[0] <= hi]


def dt(t) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(t or 0, datetime.timezone.utc)


def by_day(trades: list[dict]) -> dict[datetime.date, dict]:
    """Группируем ЗАКРЫТЫЕ круги по дню ВХОДА. Овернайта нет, поэтому круг всегда
    внутри одного дня — но страховка первого бара может закрыть остаток утром,
    такой филл относим к дню входа, иначе день получит чужой результат."""
    days: dict[datetime.date, dict] = {}
    pos, avg, opened, fills, peak = 0, 0.0, None, [], 0
    for t in sorted(trades, key=lambda x: x["time"] or 0):
        q = t["qty"] * (1 if t["side"] == "buy" else -1)
        if pos == 0:
            opened, fills, avg, peak = t["time"], [], t["price"], abs(q)
        fills.append(t)
        if pos == 0 or (pos > 0) == (q > 0):
            tot = abs(pos) + abs(q)
            avg = (avg * abs(pos) + t["price"] * abs(q)) / tot if pos else t["price"]
            pos += q
            peak = max(peak, abs(pos))
        else:
            closed = min(abs(pos), abs(q))
            pts = (t["price"] - avg) * (1 if pos > 0 else -1) * closed
            pos += q
            if pos == 0:
                d = dt(opened).date()
                rec = days.setdefault(d, {"pts": 0.0, "peak": 0, "fills": [], "n": 0,
                                          "dir": "ШОРТ" if fills[0]["side"] == "sell" else "ЛОНГ"})
                rec["pts"] += pts
                rec["peak"] = max(rec["peak"], peak)
                rec["fills"] = list(fills)
                rec["n"] += 1
                fills = []
    return days


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("secid", nargs="?", default="RIU6", choices=list(WINDOWS))
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--minutes", type=int, default=2, help="сколько дней разобрать поминутно")
    args = ap.parse_args()

    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    bars = load(args.secid)
    a, b = WINDOWS[args.secid]
    p = {**LEADER, "symbol": args.secid}
    res = await run_single_backtest(mod, bars, args.secid, p, point_value=1.0)

    print(f"######## {args.secid}  {a}..{b}  ({len(bars)} баров) ########")
    print(f"net={res['net_profit']:,.0f}  сделок={res['total_trades']}  "
          f"win={res['win_rate']:.2f}  MAE={res['max_mae']:,.0f}  пик={res['peak_contracts']}")
    print(f"защита от проскальзывания {LEADER['slip_guard_pts']:.0f} пт, "
          f"слип стопов {LEADER['slip_pct'] / 100:.2f}%")

    days = by_day(res["trades"])
    if not days:
        print("\nсделок нет")
        return
    tot = sum(v["pts"] for v in days.values())
    plus = [d for d, v in days.items() if v["pts"] > 0]
    minus = [d for d, v in days.items() if v["pts"] < 0]
    order = sorted(days, key=lambda d: -days[d]["pts"])
    top3 = sum(days[d]["pts"] for d in order[:3])

    print(f"\n=== 1. СВОДКА ПО ДНЯМ ===")
    print(f"торговали {len(days)} дней: {len(plus)} плюсовых, {len(minus)} минусовых")
    print(f"сумма по дням {tot:,.0f} пунктов")
    print(f"три лучших дня дают {top3:,.0f} = {100 * top3 / tot if tot else 0:.0f}% итога")
    worst3 = sum(days[d]["pts"] for d in order[-3:])
    print(f"три худших дня дают {worst3:,.0f}")

    print(f"\n=== 2. ХИТ-ПАРАД: {args.top} ПРИБЫЛЬНЫХ ===")
    print(f"{'дата':<13}{'дн':>4}{'напр':>6}{'пик':>5}{'кругов':>8}{'пункты':>12}")
    for d in order[:args.top]:
        v = days[d]
        print(f"{d.isoformat():<13}{RU_WD[d.weekday()]:>4}{v['dir']:>6}{v['peak']:>5}"
              f"{v['n']:>8}{v['pts']:>12,.0f}")

    print(f"\n=== 2б. ХИТ-ПАРАД: {args.top} ЗАСАДНЫХ ===")
    print(f"{'дата':<13}{'дн':>4}{'напр':>6}{'пик':>5}{'кругов':>8}{'пункты':>12}")
    for d in order[::-1][:args.top]:
        v = days[d]
        print(f"{d.isoformat():<13}{RU_WD[d.weekday()]:>4}{v['dir']:>6}{v['peak']:>5}"
              f"{v['n']:>8}{v['pts']:>12,.0f}")

    print(f"\n=== 3. ПОМИНУТНО ===")
    bmap = {x.time: x for x in bars}
    show = order[:args.minutes] + order[::-1][:args.minutes]
    for d in show:
        v = days[d]
        tag = "ЛУЧШИЙ" if v["pts"] > 0 else "ЗАСАДНЫЙ"
        print(f"\n--- {tag} {d} ({RU_WD[d.weekday()]}), {v['dir']}, пик {v['peak']} контр., "
              f"{v['pts']:,.0f} пунктов ---")
        print(f"{'время':<7}{'действие':<9}{'кол':>4}{'цена':>11}{'поз':>6}   бар O/H/L/C")
        pos = 0
        for f in v["fills"]:
            pos += f["qty"] * (1 if f["side"] == "buy" else -1)
            bar = bmap.get(f["time"])
            ohlc = (f"{bar.open:,.0f}/{bar.high:,.0f}/{bar.low:,.0f}/{bar.close:,.0f}"
                    if bar else "-")
            act = "ПОКУПКА" if f["side"] == "buy" else "ПРОДАЖА"
            print(f"{dt(f['time']):%H:%M}  {act:<9}{f['qty']:>4}{f['price']:>11,.0f}{pos:>6}   {ohlc}")

    print(f"\n=== 4. ПО ДНЯМ НЕДЕЛИ ===")
    print(f"{'день':<6}{'дней':>6}{'плюс':>6}{'минус':>7}{'пункты':>12}")
    for wd in range(7):
        dd = [d for d in days if d.weekday() == wd]
        if not dd:
            continue
        s = sum(days[d]["pts"] for d in dd)
        pp = sum(1 for d in dd if days[d]["pts"] > 0)
        print(f"{RU_WD[wd]:<6}{len(dd):>6}{pp:>6}{len(dd) - pp:>7}{s:>12,.0f}")


if __name__ == "__main__":
    asyncio.run(main())
