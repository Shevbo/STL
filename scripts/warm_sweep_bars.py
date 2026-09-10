#!/usr/bin/env python3
"""Освежить agent_bars/<базовый код>.json — непрерывную серию для перебора.

ЗАЧЕМ. Кэш базовых кодов (RI, Si, GZ) заморожен со 02.08.2026: все три файла
кончаются 31.07.2026. Это первый источник баров для i9 (`_bars_for`), поэтому
задание с окном за 31.07 молча считалось по укороченной истории: ночной перебор
09.09 (33 960 строк по RI) и walk-forward rich_fool отвечали цифрой по семи
неделям вместо девяти. Страж покрытия в `opt_agent._bars_for` починен (проверяет
теперь ОБА края окна), но без свежего файла каждое такое задание уходит в ISS,
а с сети i9 этот путь медленный и рвётся.

ЧТО ДЕЛАЕТ. Дочитывает хвост с ISS (`load_bars_iss` строит непрерывную серию с
перекатом на ближний контракт) и ЗАМЕНЯЕТ строки от SEAM вправо — всё, что
левее, остаётся нетронутым, поэтому длинная история не «плывёт» от повторной
выборки. Запись атомарная (tmp + os.replace), предыдущая версия остаётся рядом
как `<код>.json.bak`. Пустой ответ ISS файл НЕ перезаписывает.

    cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a
    POETRY_VIRTUALENVS_PATH= PYTHONPATH=. $PY scripts/warm_sweep_bars.py
    PYTHONPATH=. $PY scripts/warm_sweep_bars.py --only RI
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.lab.iss_loader import load_bars_iss  # noqa: E402

BARS_DIR = "agent_bars"
DEFAULT_SYMBOLS = ["RI", "Si", "GZ"]
# Насколько назад перезаписываем: 45 дней перекрытия хватает, чтобы шов лёг
# внутрь уже существующего куска, а не в единственный день стыка.
SEAM_DAYS = 45
# Свежий файл обязан доходить почти до сегодня: иначе лучше ничего не менять.
FRESH_DAYS = 4


def _load(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rows"]


def _save_atomic(path: str, key: str, rows: list) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{key}.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"key": key, "rows": rows}, f)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


async def refresh(symbol: str, until: dt.date) -> int:
    path = os.path.join(BARS_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        print(f"{symbol}: файла нет — пропускаю (первую заливку делает человек)")
        return 0
    rows = _load(path)
    if not rows:
        print(f"{symbol}: файл пуст — не трогаю")
        return 0
    last = rows[-1][0]
    seam = last - SEAM_DAYS * 86400
    seam_date = dt.datetime.fromtimestamp(seam, tz=dt.timezone.utc).date()
    print(f"{symbol}: в файле {len(rows)} строк до "
          f"{dt.datetime.fromtimestamp(last, tz=dt.timezone.utc):%d.%m.%Y %H:%M}, "
          f"дочитываю с {seam_date:%d.%m.%Y}")

    bars = await load_bars_iss(symbol, seam_date, until, interval=1)
    fresh = [[b.time, b.open, b.high, b.low, b.close, b.volume] for b in bars]
    if not fresh:
        print(f"{symbol}: ISS не отдал баров — файл не тронут")
        return 0
    tail_ts = fresh[-1][0]
    if tail_ts < int(dt.datetime.combine(
            until - dt.timedelta(days=FRESH_DAYS), dt.time(0, 0),
            tzinfo=dt.timezone.utc).timestamp()):
        print(f"{symbol}: ISS отдал хвост только до "
              f"{dt.datetime.fromtimestamp(tail_ts, tz=dt.timezone.utc):%d.%m.%Y} — "
              f"короче ожидаемого, файл не тронут")
        return 0

    keep = [r for r in rows if r[0] < seam]
    merged = keep + [r for r in fresh if r[0] >= seam]
    merged.sort(key=lambda r: r[0])
    # Дубли по метке: одна и та же минута могла прийти из обеих выборок.
    dedup = [merged[0]] if merged else []
    for r in merged[1:]:
        if r[0] != dedup[-1][0]:
            dedup.append(r)
    if len(dedup) <= len(keep):
        print(f"{symbol}: после склейки не прибавилось строк — файл не тронут")
        return 0

    shutil.copy2(path, path + ".bak")
    _save_atomic(path, symbol, dedup)
    print(f"{symbol}: {len(rows)} -> {len(dedup)} строк, хвост до "
          f"{dt.datetime.fromtimestamp(dedup[-1][0], tz=dt.timezone.utc):%d.%m.%Y %H:%M} "
          f"(бэкап: {symbol}.json.bak)")
    return len(dedup) - len(rows)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=DEFAULT_SYMBOLS,
                    help="базовые коды (по умолчанию RI Si GZ)")
    ap.add_argument("--until", default="",
                    help="правая граница YYYY-MM-DD (по умолчанию — сегодня)")
    args = ap.parse_args()
    until = (dt.date.fromisoformat(args.until) if args.until
             else dt.date.today() + dt.timedelta(days=1))
    total = 0
    for sym in args.only:
        try:
            total += await refresh(sym, until)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: сбой ({exc}) — файл не тронут")
    print(f"итого строк добавлено: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
