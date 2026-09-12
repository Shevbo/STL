"""Аудит минутных котировок перед перебором rich_fool. Три вопроса оператора:

  1. «Убедись что скачал котировки минутки полностью без разрывов» —
     считаем пропуски внутри торговых сессий, а не между ними.
  2. «Не обманывайся на экспирации и склейках» —
     ищем швы непрерывной серии: сутки, где цена открытия прыгает относительно
     вчерашнего закрытия на величину, которой рынок за ночь не делает.
  3. Сессии выходных (10:00-19:00) против будней (07:00-23:50) — есть ли бары.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_data_audit.py [SYMBOL ...]
"""
from __future__ import annotations

import datetime
import json
import sys

D_FROM, D_TO = "2026-03-09", "2026-09-09"


def load(symbol: str):
    rows = json.load(open(f"agent_bars/{symbol}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(D_FROM).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(D_TO).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    rows = [r for r in rows if lo <= r[0] <= hi]
    rows.sort(key=lambda r: r[0])
    return rows


def day_of(ts: int) -> datetime.date:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()


def hm(ts: int) -> int:
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return d.hour * 60 + d.minute


def audit(symbol: str) -> None:
    rows = load(symbol)
    if not rows:
        print(f"\n######## {symbol}: НЕТ ДАННЫХ в окне {D_FROM}..{D_TO}")
        return
    by_day: dict[datetime.date, list] = {}
    for r in rows:
        by_day.setdefault(day_of(r[0]), []).append(r)
    days = sorted(by_day)

    print(f"\n######## {symbol}: {len(days)} дней, {len(rows)} баров "
          f"({days[0]} .. {days[-1]}) ########")

    # --- 1. пропуски минут ВНУТРИ дня ---
    worst = []
    total_gap = 0
    for d in days:
        ms = sorted(hm(r[0]) for r in by_day[d])
        span = ms[-1] - ms[0] + 1
        missing = span - len(ms)
        total_gap += missing
        if missing:
            # самая длинная непрерывная дыра
            big = max((ms[i + 1] - ms[i] - 1) for i in range(len(ms) - 1)) if len(ms) > 1 else 0
            worst.append((missing, big, d, ms[0], ms[-1]))
    worst.sort(reverse=True)
    print(f"\n1) ПРОПУСКИ МИНУТ внутри дней: всего {total_gap}, дней с дырами {len(worst)}/{len(days)}")
    print("   худшие 8 (пропущено / макс.дыра / дата / от..до):")
    for miss, big, d, a, b in worst[:8]:
        print(f"     {miss:>5} мин, дыра {big:>4} мин, {d}  {a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}")

    # --- 2. швы склейки: скачок open против прошлого close ---
    print("\n2) ШВЫ СКЛЕЙКИ/ЭКСПИРАЦИИ (скачок открытия против вчерашнего закрытия)")
    jumps = []
    for i in range(1, len(days)):
        prev_close = by_day[days[i - 1]][-1][4]
        cur_open = by_day[days[i]][0][1]
        if prev_close <= 0:
            continue
        pct = 100.0 * (cur_open - prev_close) / prev_close
        jumps.append((abs(pct), pct, days[i], prev_close, cur_open))
    jumps.sort(reverse=True)
    print("   топ-8 по величине скачка:")
    for _, pct, d, pc, op in jumps[:8]:
        flag = "  <== ШОВ" if abs(pct) >= 3.0 else ""
        print(f"     {d}  {pc:>9,.0f} -> {op:>9,.0f}   {pct:>+7.2f}%{flag}")
    seams = [j for j in jumps if j[0] >= 3.0]
    print(f"   скачков >= 3%: {len(seams)}  (это границы контрактов, позиция через них "
          f"даёт ФАНТОМНЫЙ результат)")

    # --- 3. сессии: будни vs выходные ---
    print("\n3) СЕССИИ")
    wd = [d for d in days if d.weekday() < 5]
    we = [d for d in days if d.weekday() >= 5]

    def cover(dd, lo_m, hi_m):
        return sum(1 for d in dd if any(lo_m <= hm(r[0]) <= hi_m for r in by_day[d]))

    print(f"   будни {len(wd)} дней: бар в 06:50-06:59 = {cover(wd, 410, 419):>3}, "
          f"07:00-07:30 = {cover(wd, 420, 450):>3}, 10:00-10:30 = {cover(wd, 600, 630):>3}, "
          f"23:00-23:50 = {cover(wd, 1380, 1430):>3}")
    print(f"   выходные {len(we)} дней: бар в 09:50-09:59 = {cover(we, 590, 599):>3}, "
          f"10:00-10:30 = {cover(we, 600, 630):>3}, 18:00-19:00 = {cover(we, 1080, 1140):>3}")
    if we:
        first_we = sorted({min(hm(r[0]) for r in by_day[d]) for d in we})[:4]
        last_we = sorted({max(hm(r[0]) for r in by_day[d]) for d in we})[-4:]
        print(f"   выходные: первые минуты {[f'{m // 60:02d}:{m % 60:02d}' for m in first_we]}, "
              f"последние {[f'{m // 60:02d}:{m % 60:02d}' for m in last_we]}")


def main() -> None:
    syms = sys.argv[1:] or ["RI", "Si"]
    for s in syms:
        audit(s)


if __name__ == "__main__":
    main()
