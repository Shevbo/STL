"""Поминутный разбор rich_fool и финрез по ПРИЧИНАМ выхода.

Два режима:
  без даты — весь квартал: каждый выход помечен причиной (стоп / тейк / 2 EMA /
             закрытие сессии / утренняя страховка), итог в пунктах по причинам и
             список дней. Отвечает на «если стоп не срабатывает, откуда финрез».
  с датой  — один день по минутам: где ступени, стоп, тейк, что сделал каждый бар.

Запускается РЕАЛЬНАЯ стратегия. Причину выхода стратегия не сохраняет, поэтому она
восстанавливается здесь по состоянию ДО бара в том же порядке проверок, что в
rich_fool.on_bar: новый день -> закрытие сессии -> стоп -> 2 EMA -> тейк.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_day_walk.py RIM6 [2026-05-27] [--hold 30 ...]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import importlib
import json

from trader.lab.runtime import Bar, BacktestRuntime

WINDOWS = {
    "RIM6": ("2026-03-20", "2026-06-17"),
    "RIU6": ("2026-06-19", "2026-09-09"),
    "SiM6": ("2026-03-20", "2026-06-17"),
    "SiU6": ("2026-06-19", "2026-09-09"),
    "RIZ5": ("2025-09-19", "2025-12-18"),     # вне выборки отбора rf10
    "RIH6": ("2025-12-19", "2026-03-19"),
}

# Верх топа грубой сетки rf8 на RIM6 (12.09 23:15), d_coef выведен калибровкой.
LEADER = dict(f_shift=100, n_days=4, hold_min=30, sl_beyond_pts=150, tp_arm_pts=200,
              tp_back_pts=150, qty_first=2, max_contracts=40, d_coef=216, step_count=20,
              slip_guard_pts=50, slip_pct=0, place_lead_min=10, ema_fast=9, ema_slow=21,
              exit_lead_min=120, time_exit_min=0, invert=0, allow_long=1, allow_short=1, bar_offset_min=0)

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


def stop_px(st: dict, avg: float, dirn: int, beyond: float) -> float:
    up = [float(x) for x in (st.get("levels_up") or [])]
    dn = [float(x) for x in (st.get("levels_dn") or [])]
    return min([avg] + dn) - beyond if dirn > 0 else max([avg] + up) + beyond


def exit_reason(bar: Bar, st: dict, avg: float, dirn: int, p: dict) -> str:
    """Причина выхода по состоянию ДО бара — порядок проверок как в стратегии."""
    hm = bar.time % 86400 // 60
    if st.get("day") != bar.time // 86400:
        return "утро"
    close_hm = int(st.get("close_hm") or 0) or 23 * 60 + 50
    if hm >= close_hm:
        return "закрытие"
    sl = stop_px(st, avg, dirn, float(p["sl_beyond_pts"]))
    if (bar.low <= sl) if dirn > 0 else (bar.high >= sl):
        return "стоп"
    lf = int(st.get("last_fill_t") or 0)
    if p.get("time_exit_min") and lf and bar.time - lf >= p["time_exit_min"] * 60:
        return "время"
    if p["exit_lead_min"] and hm >= close_hm - int(p["exit_lead_min"]):
        return "2ema"          # тейк в этом окне тоже возможен, но 2 EMA проверяется раньше
    return "тейк"


async def walk(secid: str, p: dict):
    """Прогон с записью: (бар, позиция после, средняя после, состояние после, новые
    ордера, причина выхода или '')."""
    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    bars = load(secid)
    rt = BacktestRuntime(bars=bars, symbol=secid, initial_equity=1_000_000.0)
    out, n_orders = [], 0
    while True:
        bar = bars[rt._cursor]
        before = rt._positions.get(secid, {"side": "flat", "qty": 0, "avg": 0.0})
        q0 = before["qty"] if before["side"] == "long" else (-before["qty"] if before["side"] == "short" else 0)
        avg0, st0 = float(before["avg"]), dict(rt._state)
        await mod.on_bar(rt, p)
        new = rt._orders[n_orders:]
        n_orders = len(rt._orders)
        pos = rt._positions.get(secid, {"side": "flat", "qty": 0, "avg": 0.0})
        q1 = pos["qty"] if pos["side"] == "long" else (-pos["qty"] if pos["side"] == "short" else 0)
        why = ""
        if q0 and q1 == 0:
            why = exit_reason(bar, st0, avg0, 1 if q0 > 0 else -1, p)
        out.append((bar, q1, float(pos["avg"]), dict(rt._state), new, why, q0, avg0))
        if not rt.advance():
            break
    return out


def summary(secid: str, rows, p: dict) -> None:
    a, b = WINDOWS[secid]
    by_reason: dict[str, list] = {}
    days = []
    for bar, _q1, _a1, _st, new, why, q0, avg0 in rows:
        if not why:
            continue
        px = new[-1].price if new else bar.close
        pts = (px - avg0) * q0                      # q0 со знаком: лонг +, шорт −
        by_reason.setdefault(why, []).append(pts)
        days.append((dt(bar.time), why, q0, avg0, px, pts))
    tot = sum(x[5] for x in days)
    print(f"######## {secid} {a}..{b} ########")
    print("параметры: " + ", ".join(f"{k}={v}" for k, v in p.items()
                                    if k not in ("allow_long", "allow_short", "bar_offset_min", "symbol")))
    print(f"\nдней с позицией {len(days)}, итог {tot:,.0f} пунктов×контракты (без комиссии)")
    print(f"\n{'причина':<10}{'раз':>5}{'плюс':>6}{'пункты':>12}{'средний':>10}")
    for why, v in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"{why:<10}{len(v):>5}{sum(1 for x in v if x > 0):>6}{sum(v):>12,.0f}{sum(v) / len(v):>10,.0f}")
    print(f"\n{'дата':<12}{'дн':>3}{'выход':>6}{'причина':>10}{'поз':>5}{'средняя':>10}{'выход по':>10}{'пункты':>10}")
    for t, why, q0, avg0, px, pts in days:
        print(f"{t:%Y-%m-%d}{RU_WD[t.weekday()]:>3} {t:%H:%M}{why:>10}{q0:>5}{avg0:>10,.0f}{px:>10,.0f}{pts:>10,.0f}")


def day(secid: str, target: datetime.date, rows, p: dict, after: int) -> None:
    rows = [r for r in rows if dt(r[0].time).date() == target]
    if not rows:
        print(f"{secid}: за {target} баров нет")
        return
    guard = float(p["slip_guard_pts"])
    armed = next((r[3] for r in rows if r[3].get("levels_up")), None)
    print(f"######## {secid}  {target} ({RU_WD[target.weekday()]}) ########")
    if armed:
        up = [float(x) for x in armed["levels_up"]]
        dn = [float(x) for x in armed["levels_dn"]]
        mid = (up[0] + dn[0]) / 2
        print(f"вчерашнее закрытие ~{mid:,.0f}; ступеней {len(up)}")
        print(f"ШОРТ вверх: {', '.join(f'{x:,.0f}' for x in up)}")
        print(f"ЛОНГ вниз:  {', '.join(f'{x:,.0f}' for x in dn)}")
        print(f"стоп шорта {up[-1] + p['sl_beyond_pts']:,.0f}, стоп лонга {dn[-1] - p['sl_beyond_pts']:,.0f}; "
              f"налив лимитки: проход {guard:.0f} пт за уровень")
        ce, we = int(armed.get("close_hm") or 0), int(armed.get("win_end") or 0)
        print(f"окно набора до {we // 60:02d}:{we % 60:02d}, закрытие сессии {ce // 60:02d}:{ce % 60:02d}, "
              f"2 EMA с {(ce - p['exit_lead_min']) // 60:02d}:{(ce - p['exit_lead_min']) % 60:02d}")

    first = next((i for i, r in enumerate(rows) if r[4]), None)
    last = max((i for i, r in enumerate(rows) if r[4]), default=None)
    if first is None:
        print("\nсделок в этот день нет")
        return
    rows = rows[max(0, first - 3):min(len(rows), last + 1 + after)]
    print(f"\n{'время':<6}{'O':>8}{'H':>8}{'L':>8}{'C':>8}{'поз':>5}{'средняя':>9}{'стоп':>9}"
          f"{'тейк':>12}  событие")
    for bar, q1, avg, st, new, why, _q0, _a0 in rows:
        dirn = int(st.get("dir") or 0)
        sl_s = tp_s = "-"
        if q1 and avg > 0 and dirn:
            sl_s = f"{stop_px(st, avg, dirn, float(p['sl_beyond_pts'])):,.0f}"
            if int(st.get("tp_armed") or 0):
                best = float(st.get("tp_best") or 0)
                tp_s = f"{best - p['tp_back_pts'] if dirn > 0 else best + p['tp_back_pts']:,.0f}"
            else:
                tp_s = f"акт {avg + p['tp_arm_pts'] if dirn > 0 else avg - p['tp_arm_pts']:,.0f}"
        note = ""
        for o in new:
            note += f"{'КУПИЛ' if o.side == 'buy' else 'ПРОДАЛ'} {o.qty} по {o.price:,.0f}  "
        if why:
            note += f"<< ВЫХОД: {why}"
        print(f"{dt(bar.time):%H:%M}{bar.open:>8,.0f}{bar.high:>8,.0f}{bar.low:>8,.0f}{bar.close:>8,.0f}"
              f"{q1:>5}{avg if avg else 0:>9,.0f}{sl_s:>9}{tp_s:>12}  {note}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("secid", choices=list(WINDOWS))
    ap.add_argument("date", nargs="?")
    ap.add_argument("--after", type=int, default=3, help="баров после выхода")
    for k in LEADER:
        ap.add_argument(f"--{k}", type=int)
    args = ap.parse_args()
    p = {**LEADER, **{k: getattr(args, k) for k in LEADER if getattr(args, k) is not None},
         "symbol": args.secid}
    rows = await walk(args.secid, p)
    if args.date:
        day(args.secid, datetime.date.fromisoformat(args.date), rows, p, args.after)
    else:
        summary(args.secid, rows, p)


if __name__ == "__main__":
    asyncio.run(main())
