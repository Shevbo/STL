#!/usr/bin/env python3
"""Скачать ПОЛНЫЕ минутки ПО КОНТРАКТАМ (без склейки) в agent_bars/<SECID>.json.

ЗАЧЕМ. Базовые коды (RI, Si) в кэше — НЕПРЕРЫВНАЯ серия, сшитая через перекат без
выравнивания базиса. Позиция, пережившая шов, получает фантомный результат: в окне
2026-03..09 у RI ровно один шов 01.07.2026 (105 250 -> 91 560, −13.01%), и ночной
«лидер» rich_fool на 2.2 млн сделал 96% итога ровно на нём. Поконтрактный прогон
убирает этот класс ошибок целиком: внутри одного контракта швов нет.

Плюс базовый кэш собран источником, который терял УТРЕННЮЮ сессию: окно 07:00-07:30
есть лишь в 42 днях из 155. ISS её отдаёт (проверено: 344 бара в 07:xx за 6 дней),
поэтому качаем заново прямо у ISS.

ЗАПУСК:
    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    PYTHONPATH=. $PY scripts/rf_fetch_contracts.py RIH6 RIM6 RIU6 SiH6 SiM6 SiU6
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.lab.iss_loader import IssLoader  # noqa: E402

BARS_DIR = "agent_bars"
# Контракт живёт ~9 месяцев, но ликвиден последние ~3. Берём с запасом назад от
# последнего торгового дня — лишнее не мешает, окно прогона режется отдельно.
LOOKBACK_DAYS = 200


def _write(path: str, rows: list) -> None:
    """Атомарная запись: tmp + os.replace, прежняя версия остаётся как .bak."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"rows": rows}, f, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _audit(rows: list) -> str:
    """Коротко: дней, баров, дыры внутри сессий, покрытие предоткрытия."""
    by_day: dict[dt.date, list[int]] = {}
    for r in rows:
        d = dt.datetime.fromtimestamp(r[0], dt.timezone.utc)
        by_day.setdefault(d.date(), []).append(d.hour * 60 + d.minute)
    if not by_day:
        return "пусто"
    days = sorted(by_day)
    gaps = 0
    for d in days:
        ms = sorted(by_day[d])
        gaps += (ms[-1] - ms[0] + 1) - len(ms)
    wd = [d for d in days if d.weekday() < 5]
    we = [d for d in days if d.weekday() >= 5]
    pre_wd = sum(1 for d in wd if any(405 <= m <= 419 for m in by_day[d]))
    pre_we = sum(1 for d in we if any(585 <= m <= 599 for m in by_day[d]))
    return (f"{len(days)} дней ({days[0]}..{days[-1]}), {len(rows)} баров, "
            f"неторг.минут внутри сессий {gaps}; предоткрытие: будни {pre_wd}/{len(wd)}, "
            f"выходные {pre_we}/{len(we)}")


async def fetch(secid: str) -> None:
    # SECID:YYYY-MM-DD — последний торговый день задан явно. У истёкших контрактов ISS
    # не отдаёт LASTTRADEDATE, и окно «по сегодня» срезало RIH6 до 21 дня, RIZ5 до нуля.
    secid, _, forced = secid.partition(":")
    async with IssLoader() as ld:
        meta = None if forced else await ld.get_security_meta(secid)
        ltd = dt.date.fromisoformat(forced) if forced else None
        if meta:
            raw = meta.get("LASTTRADEDATE") or meta.get("lasttradedate")
            if raw:
                try:
                    ltd = dt.date.fromisoformat(str(raw)[:10])
                except ValueError:
                    ltd = None
        if ltd is None:
            print(f"  {secid}: LASTTRADEDATE неизвестна — беру окно по сегодня")
            ltd = dt.date.today()
        d_from = ltd - dt.timedelta(days=LOOKBACK_DAYS)
        bars = await ld.fetch_contract_bars(secid, d_from, ltd, interval=1)

    if not bars:
        print(f"  {secid}: ISS вернул ПУСТО за {d_from}..{ltd} — файл не трогаю")
        return
    rows = [[b.time, b.open, b.high, b.low, b.close, b.volume] for b in bars]
    rows.sort(key=lambda r: r[0])
    path = os.path.join(BARS_DIR, f"{secid}.json")
    if os.path.exists(path):
        old = len(json.load(open(path))["rows"])
        if old > len(rows):
            print(f"  {secid}: новый набор КОРОЧЕ ({len(rows)} < {old} баров) — файл не трогаю; {_audit(rows)}")
            return
    _write(path, rows)
    print(f"  {secid}: последний торговый день {ltd}; {_audit(rows)}")


async def main() -> None:
    secids = sys.argv[1:] or ["RIH6", "RIM6", "RIU6", "SiH6", "SiM6", "SiU6"]
    print(f"качаю поконтрактные минутки: {', '.join(secids)}")
    for s in secids:
        try:
            await fetch(s)
        except Exception as exc:                       # noqa: BLE001
            print(f"  {s}: ОШИБКА {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
