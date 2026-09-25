#!/usr/bin/env python3
"""Импорт выгрузки таблицы всех сделок QUIK в архив рынка.

    python scripts/import_quik_trades.py выгрузка.csv [--out /home/ubuntu/market-archive]

ЗАЧЕМ. 25.09.2026 сбор данных стоял восемь торговых часов: сначала пропал агент,
потом остановился Lua-скрипт. В архиве за эти часы нет ни стаканов, ни тиков, а
ленты обезличенных сделок у нас не было НИКОГДА — она идёт в раннер для баров и
на диск не пишется.

ЧТО ЭТОТ ИМПОРТ ДАЁТ И ЧЕГО НЕ ДАЁТ. Стакан из ленты не восстанавливается в
принципе: лента — это состоявшиеся сделки, а стакан — заявки, которые НЕ
исполнились. Объёмы на уровнях, где сделок не было, ленте неизвестны; заявка,
простоявшая час и снятая, следа не оставляет. Из ленты восстанавливаются цена,
объём, интенсивность и — по агрессорам — грубая оценка спреда. Для модели
исполнения это приближение, а не замена архива стакана.

ФОРМАТ. QUIK экспортирует таблицу с заголовком; разделитель и набор колонок
зависят от настроек терминала, поэтому колонки ищутся ПО ИМЕНИ, регистр и
пробелы не важны. Нужны: инструмент, цена, количество, время. Необязательные:
дата (иначе берётся из имени файла или из --date) и операция (купля/продажа).

Пишет строки формата архива (`trade-YYYY-MM-DD.jsonl`), не трогая существующие
потоки. Повторный запуск того же файла не задваивает: дубли отсеиваются по
номеру сделки, а без него — по (время, инструмент, цена, объём).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys

MSK = datetime.timezone(datetime.timedelta(hours=3))

# Имя колонки -> наш ключ. QUIK зовёт их по-русски, набор зависит от настроек.
COLUMNS = {
    "код инструмента": "sec", "инструмент": "sec", "бумага": "sec", "sec_code": "sec",
    "sec": "sec", "code": "sec", "seccode": "sec", "класс": None,
    "цена": "price", "price": "price",
    "кол-во": "qty", "количество": "qty", "qty": "qty", "объём": "qty", "объем": "qty",
    "время": "time", "time": "time",
    "дата": "date", "date": "date",
    "операция": "side", "направление": "side", "buysell": "side", "operation": "side",
    "номер": "num", "номер сделки": "num", "trade_num": "num",
}


# База FORTS -> код инструмента. Выгрузка таблицы сделок даёт НАЗВАНИЕ
# ("RTS-12.26 [ФОРТС фьючерсы]"), а архив живёт на кодах (RIZ6).
_BASES = {"RTS": "RI", "SI": "Si", "BR": "BR", "GOLD": "GD", "GAZR": "GZ",
          "MIX": "MX", "SBRF": "SR", "MXI": "MX", "NG": "NG", "ED": "ED"}
_MONTH = "FGHJKMNQUVXZ"      # январь..декабрь, коды месяцев фьючерсов


def code_from_title(title: str) -> str:
    """'RTS-12.26 [ФОРТС фьючерсы]' -> 'RIZ6'. Не разобрали — отдаём как есть."""
    t = (title or "").split("[")[0].strip()
    if "-" not in t:
        return t
    base, _, tail = t.partition("-")
    mm, _, yy = tail.partition(".")
    b = _BASES.get(base.strip().upper())
    try:
        m, y = int(mm), int(yy)
    except ValueError:
        return t
    if not b or not 1 <= m <= 12:
        return t
    return f"{b}{_MONTH[m - 1]}{y % 10}"


def parse_tail_row(line: str) -> tuple[str, str, float, int, str] | None:
    """Разбор строки, где ЦЕНА содержит тот же разделитель, что и поля.

    QUIK экспортирует «96,52» (цена) внутри CSV с запятыми, поэтому колонок в
    строке больше, чем в заголовке, и позиционный разбор слева ломается. Читаем
    С КОНЦА: операция, количество, а всё между инструментом и количеством —
    цена, склеенная из одного или двух кусков."""
    parts = line.rstrip().rstrip(",").split(",")
    if len(parts) < 6:
        return None
    op, qty_s = parts[-1].strip(), parts[-2].strip()
    price_s = ".".join(x.strip() for x in parts[3:-2])
    price_s = price_s.replace(" ", "").replace(" ", "").replace(" ", "")
    try:
        price, qty = float(price_s), int(qty_s)
    except ValueError:
        return None
    return parts[1].strip(), code_from_title(parts[2]), price, qty, op


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _sniff(path: str) -> str:
    with open(path, encoding="cp1251", errors="replace") as fh:
        head = fh.readline()
    for sep in (";", "\t", ","):
        if head.count(sep) >= 3:
            return sep
    return ";"


def _parse_ms(date_s: str, time_s: str, fallback_date: str) -> int:
    d = (date_s or fallback_date or "").strip()
    t = (time_s or "").strip()
    if not t:
        return 0
    for dfmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        for tfmt in ("%H:%M:%S", "%H:%M:%S.%f", "%H:%M"):
            try:
                dt = datetime.datetime.strptime(f"{d} {t}", f"{dfmt} {tfmt}")
                return int(dt.replace(tzinfo=MSK).timestamp() * 1000)
            except ValueError:
                continue
    return 0


def _number(s: str) -> float:
    return float((s or "0").replace("\xa0", "").replace(" ", "").replace(",", "."))


def convert(path: str, fallback_date: str) -> list[dict]:
    with open(path, encoding="cp1251", errors="replace") as fh:
        header = fh.readline()
    if "Инструмент" in header and "Цена" in header and header.count(",") >= 4:
        return _convert_tail(path, fallback_date)
    sep = _sniff(path)
    rows: list[dict] = []
    with open(path, encoding="cp1251", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=sep)
        header = next(reader, None)
        if not header:
            return rows
        idx = {}
        for i, name in enumerate(header):
            key = COLUMNS.get(_norm(name))
            if key and key not in idx:  # первое вхождение имени выигрывает
                idx[key] = i
        missing = [k for k in ("sec", "price", "qty", "time") if k not in idx]
        if missing:
            raise SystemExit(
                f"в выгрузке нет колонок: {', '.join(missing)}. "
                f"Заголовок: {', '.join(header)}")
        for r in reader:
            if len(r) <= max(idx.values()):
                continue
            try:
                price, qty = _number(r[idx["price"]]), int(_number(r[idx["qty"]]))
            except ValueError:
                continue
            if price <= 0 or qty <= 0:
                continue
            ts = _parse_ms(r[idx["date"]] if "date" in idx else "",
                           r[idx["time"]], fallback_date)
            if not ts:
                continue
            side_raw = _norm(r[idx["side"]]) if "side" in idx else ""
            side = 2 if side_raw.startswith(("куп", "b")) else 1 if side_raw else 0
            rows.append({
                "code": (r[idx["sec"]] or "").strip(),
                "price": price, "qty": qty, "side": side,
                "received_at_unix_ms": ts, "ts_ms": ts,
                "num": (r[idx["num"]].strip() if "num" in idx else ""),
                "source": "quik_export",
            })
    rows.sort(key=lambda x: x["ts_ms"])
    return rows


def _convert_tail(path: str, fallback_date: str) -> list[dict]:
    """Ветка для выгрузки «Время,Инструмент,Цена,Кол-во,Операция» без даты."""
    rows: list[dict] = []
    with open(path, encoding="cp1251", errors="replace") as fh:
        fh.readline()                      # заголовок
        for line in fh:
            got = parse_tail_row(line)
            if not got:
                continue
            time_s, code, price, qty, op = got
            if price <= 0 or qty <= 0 or not code:
                continue
            ts = _parse_ms("", time_s, fallback_date)
            if not ts:
                continue
            low = op.lower()
            rows.append({
                "code": code, "price": price, "qty": qty,
                "side": 2 if low.startswith("куп") else 1 if low.startswith("прод") else 0,
                "received_at_unix_ms": ts, "ts_ms": ts, "num": "",
                "source": "quik_export",
            })
    rows.sort(key=lambda x: x["ts_ms"])
    return rows


def _key(r: dict) -> str:
    return r["num"] or f'{r["ts_ms"]}:{r["code"]}:{r["price"]}:{r["qty"]}'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="файл выгрузки из QUIK (Действия -> Экспорт в файл)")
    ap.add_argument("--out", default="/home/ubuntu/market-archive")
    ap.add_argument("--date", default="", help="дата сделок ДД.ММ.ГГГГ, если её нет в файле")
    args = ap.parse_args()

    fallback = args.date or ""
    if not fallback:
        m = re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", os.path.basename(args.csv))
        if m:
            fallback = f"{m.group(3)}.{m.group(2)}.{m.group(1)}"

    rows = convert(args.csv, fallback)
    if not rows:
        print("в выгрузке не нашлось ни одной сделки")
        return 1

    by_day: dict[str, list[dict]] = {}
    for r in rows:
        day = datetime.datetime.fromtimestamp(r["ts_ms"] / 1000, MSK).date().isoformat()
        by_day.setdefault(day, []).append(r)

    os.makedirs(args.out, exist_ok=True)
    for day, items in sorted(by_day.items()):
        path = os.path.join(args.out, f"trade-{day}.jsonl")
        seen = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        seen.add(_key(json.loads(line)))
                    except ValueError:
                        continue
        fresh = [r for r in items if _key(r) not in seen]
        with open(path, "a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        codes = sorted({r["code"] for r in fresh})
        hours = sorted({datetime.datetime.fromtimestamp(r["ts_ms"] / 1000, MSK).strftime("%H")
                        for r in fresh})
        print(f"{day}: дописано {len(fresh)} из {len(items)} "
              f"(дубли пропущены: {len(items) - len(fresh)})")
        print(f"   инструменты: {', '.join(codes) or '—'}")
        print(f"   часы: {', '.join(hours) or '—'}")
    print("\nНапоминание: это ЛЕНТА, а не стакан. Глубина и стоявшие заявки "
          "по этим часам не восстановлены и восстановлены быть не могут.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
