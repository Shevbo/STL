"""Есть ли на ISS минутки ПРЕДОТКРЫТИЙНОГО и утреннего окна FORTS?

Вопрос-блокер: rich_fool выставляет заявки за 10 минут до открытия биржи, то есть
стратегии нужны бары 06:50-07:30 МСК (будни) и 09:50-10:30 (выходные). В кэше
agent_bars их почти нет — надо понять, это дефект кэша или ISS их не отдаёт.

Качаем ОДИН контракт за ОДНУ неделю напрямую с ISS и смотрим распределение по часам.

ЗАПУСК: PYTHONPATH=. $PY scripts/rf_iss_probe.py [SECID] [YYYY-MM-DD] [YYYY-MM-DD]
"""
from __future__ import annotations

import asyncio
import datetime
import sys
from collections import Counter

from trader.lab.iss_loader import IssLoader


async def main() -> None:
    secid = sys.argv[1] if len(sys.argv) > 1 else "RIU6"
    d_from = datetime.date.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else "2026-08-10")
    d_to = datetime.date.fromisoformat(sys.argv[3] if len(sys.argv) > 3 else "2026-08-17")

    async with IssLoader() as ld:
        bars = await ld.fetch_contract_bars(secid, d_from, d_to, interval=1)

    print(f"{secid}  {d_from}..{d_to}:  баров {len(bars)}")
    if not bars:
        print("ПУСТО — ISS не отдал ничего за это окно")
        return

    per_hour: Counter[int] = Counter()
    per_day: dict[datetime.date, list[int]] = {}
    for b in bars:
        d = datetime.datetime.fromtimestamp(b.time, datetime.timezone.utc)
        per_hour[d.hour] += 1
        per_day.setdefault(d.date(), []).append(d.hour * 60 + d.minute)

    print("\nбаров по часам (метка как в кэше, МСК-стенка):")
    for h in range(24):
        if per_hour[h]:
            print(f"  {h:02d}:xx  {per_hour[h]:>4d}")

    print("\nпо дням: первый бар / последний бар / всего / пропущено внутри диапазона")
    for d in sorted(per_day):
        ms = sorted(per_day[d])
        span = ms[-1] - ms[0] + 1
        wd = "вых" if d.weekday() >= 5 else "буд"
        print(f"  {d} {wd}  {ms[0] // 60:02d}:{ms[0] % 60:02d}  {ms[-1] // 60:02d}:{ms[-1] % 60:02d}"
              f"  {len(ms):>4d}  {span - len(ms):>4d}")


if __name__ == "__main__":
    asyncio.run(main())
