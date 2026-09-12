"""Поминутный разбор ОДНОГО дня rich_fool: состояние решения на каждом баре.

Отличие от rf_days.py: тот показывает филлы, а этот — ПОЧЕМУ они случились и почему
не случились раньше. На каждом баре видно, где стоят неисполненные ступени, где
стоп, где трейлинг, и какое событие бар вызвал.

Запускается РЕАЛЬНАЯ стратегия, состояние читается из рантайма после каждого бара —
логику никто не дублирует. Исключение: цены стопа и трейлинга стратегия нигде не
сохраняет, их приходится пересчитывать здесь теми же формулами. Если они разойдутся,
это поймают юнит-тесты стратегии, а не этот отчёт.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_day_walk.py RIM6 2026-05-27
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
}

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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("secid", choices=list(WINDOWS))
    ap.add_argument("date")
    ap.add_argument("--after", type=int, default=4, help="баров после выхода")
    args = ap.parse_args()
    target = datetime.date.fromisoformat(args.date)

    mod = importlib.import_module("trader.lab.strategies.rich_fool")
    bars = load(args.secid)
    p = {**LEADER, "symbol": args.secid}
    rt = BacktestRuntime(bars=bars, symbol=args.secid, initial_equity=1_000_000.0)

    sl_frac = p["sl_price_pct"] / 10000.0
    tr_frac = p["trail_tp_pct"] / 10000.0
    guard = float(p["slip_guard_pts"])

    rows = []
    n_orders = 0
    while True:
        bar = bars[rt._cursor]
        await mod.on_bar(rt, p)
        # Счётчик ордеров двигаем на КАЖДОМ баре, а не только на целевом дне: иначе
        # первая строка отчёта выгружает все сделки за всю историю до этого дня.
        new = rt._orders[n_orders:]
        n_orders = len(rt._orders)
        if dt(bar.time).date() == target:
            pos = rt._positions.get(args.secid, {"side": "flat", "qty": 0, "avg": 0.0})
            signed = pos["qty"] if pos["side"] == "long" else (-pos["qty"] if pos["side"] == "short" else 0)
            rows.append((bar, signed, float(pos["avg"]), dict(rt._state), new))
        if not rt.advance():
            break

    if not rows:
        print(f"{args.secid}: за {target} баров нет")
        return

    # параметры дня берём из состояния ПОСЛЕ вооружения
    armed = next((st for _, _, _, st, _ in rows if st.get("levels_up")), None)
    print(f"######## {args.secid}  {target} ({RU_WD[target.weekday()]}) ########")
    if armed:
        up = [float(x) for x in armed["levels_up"]]
        dn = [float(x) for x in armed["levels_dn"]]
        print(f"лестница ШОРТА (вверх): {', '.join(f'{x:,.0f}' for x in up[:8])}")
        print(f"лестница ЛОНГА (вниз):  {', '.join(f'{x:,.0f}' for x in dn[:8])}")
        print(f"зазоры вверх: {', '.join(f'{up[i + 1] - up[i]:,.0f}' for i in range(min(4, len(up) - 1)))} …")
        print(f"защита: цена обязана пройти {guard:.0f} пт ЗА уровень")
        print(f"закрытие сессии по истории: {int(armed.get('close_hm') or 0) // 60:02d}:"
              f"{int(armed.get('close_hm') or 0) % 60:02d}, "
              f"окно набора до {int(armed.get('win_end') or 0) // 60:02d}:"
              f"{int(armed.get('win_end') or 0) % 60:02d}")

    # обрезаем хвост: до выхода + args.after
    last_ev = max((i for i, r in enumerate(rows) if r[4]), default=len(rows) - 1)
    rows = rows[:min(len(rows), last_ev + 1 + args.after)]

    print(f"\n{'время':<7}{'O':>9}{'H':>9}{'L':>9}{'C':>9}{'поз':>5}{'средняя':>10}"
          f"{'стоп':>10}{'трейлинг':>10}  след.ступень / событие")
    for bar, signed, avg, st, new in rows:
        up = [float(x) for x in (st.get("levels_up") or [])]
        dn = [float(x) for x in (st.get("levels_dn") or [])]
        hit = int(st.get("hit") or 0)
        dirn = int(st.get("dir") or 0)
        peak = float(st.get("tp_peak") or 0)

        sl_s = tp_s = "-"
        if signed and avg > 0 and dirn:
            buf = avg * sl_frac
            if dirn > 0:
                sl_s = f"{min([avg] + dn) - buf:,.0f}"
            else:
                sl_s = f"{max([avg] + up) + buf:,.0f}"
            if peak:
                tp = peak - avg * tr_frac if dirn > 0 else peak + avg * tr_frac
                ok = (tp > avg) if dirn > 0 else (tp < avg)
                tp_s = f"{tp:,.0f}" + ("" if ok else "*")

        note = ""
        if up and hit < len(up):
            note = f"шорт {up[hit] + guard:,.0f} / лонг {dn[hit] - guard:,.0f}"
        elif up:
            note = "лестница исчерпана"
        for o in new:
            act = "ПОКУПКА" if o.side == "buy" else "ПРОДАЖА"
            note += f"   >>> {act} {o.qty} по {o.price:,.0f}"

        print(f"{dt(bar.time):%H:%M}{bar.open:>9,.0f}{bar.high:>9,.0f}{bar.low:>9,.0f}"
              f"{bar.close:>9,.0f}{signed:>5}{avg if avg else 0:>10,.0f}{sl_s:>10}{tp_s:>10}  {note}")
    print("\n* у трейлинга означает «ещё в убытке, поэтому не вооружён»")


if __name__ == "__main__":
    asyncio.run(main())
