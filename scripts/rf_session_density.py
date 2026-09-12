"""Плотность баров по часам и наличие ПРЕДОТКРЫТИЙНОГО окна в истории.

Суть rich_fool — выставить заявки ДО открытия биржи. Значит якорь открытия это
07:00 МСК (утренняя сессия FORTS), а арм-бар должен находиться в 06:50-06:59.
Проверяем по факту, а не по памяти: есть ли в кэше бары в этом окне и сколько.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_session_density.py
"""
from __future__ import annotations

import datetime
import json

D_FROM, D_TO = "2026-03-09", "2026-09-09"


def load_days(symbol: str):
    rows = json.load(open(f"agent_bars/{symbol}.json"))["rows"]
    lo = int(datetime.datetime.fromisoformat(D_FROM).replace(tzinfo=datetime.timezone.utc).timestamp())
    hi = int(datetime.datetime.fromisoformat(D_TO).replace(tzinfo=datetime.timezone.utc).timestamp()) + 86399
    by_day: dict[str, list[int]] = {}
    per_hour = [0] * 24
    for r in rows:
        ts = r[0]
        if ts < lo or ts > hi:
            continue
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        by_day.setdefault(d.strftime("%Y-%m-%d"), []).append(d.hour * 60 + d.minute)
        per_hour[d.hour] += 1
    return by_day, per_hour


def main() -> None:
    for sym in ("RI", "Si"):
        by_day, per_hour = load_days(sym)
        days = sorted(by_day)
        print(f"\n======== {sym}: {len(days)} дней, {sum(per_hour)} баров ========")
        print("час МСК:  " + " ".join(f"{h:>5d}" for h in range(6, 14)))
        print("баров:    " + " ".join(f"{per_hour[h]:>5d}" for h in range(6, 14)))
        print("на день:  " + " ".join(f"{per_hour[h] / len(days):>5.1f}" for h in range(6, 14)))

        # окно вооружения для open_hour=7, place_lead_min=10  ->  06:50..06:59
        arm = sum(1 for d in days if any(410 <= m <= 419 for m in by_day[d]))
        # окно вооружения, если считать "первый бар сессии" 07:00..07:09
        arm7 = sum(1 for d in days if any(420 <= m <= 429 for m in by_day[d]))
        win7 = sum(1 for d in days if any(420 <= m <= 450 for m in by_day[d]))
        win10 = sum(1 for d in days if any(600 <= m <= 630 for m in by_day[d]))
        print(f"дней с баром 06:50-06:59 (арм до открытия): {arm:>4d} из {len(days)}")
        print(f"дней с баром 07:00-07:09:                   {arm7:>4d} из {len(days)}")
        print(f"дней с баром 07:00-07:30 (окно утра):       {win7:>4d} из {len(days)}")
        print(f"дней с баром 10:00-10:30 (окно дня):        {win10:>4d} из {len(days)}")

        first = [min(by_day[d]) for d in days]
        hh = sorted(set(first))[:5]
        print(f"самые ранние минуты первого бара дня: {[f'{m // 60:02d}:{m % 60:02d}' for m in hh]}")


if __name__ == "__main__":
    main()
