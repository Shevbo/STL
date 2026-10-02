"""Уборка после торгового дня: дубли в журнале сделок и мёртвые записи книги.

Запуск: ``python -m trader.quik.cleanup`` из корня приложения. По умолчанию
ПОКАЗЫВАЕТ, что сделает; правит только с ``--apply``.

ЗАЧЕМ. Два вида мусора копятся сами и оба врут в учёте:

* **дубли в журнале сделок.** Дата сделки у QUIK — это ТОРГОВЫЙ день, и вечерняя
  сессия датируется следующим; авто-восстановление журнала вливает те же филлы во
  второй файл. 02.10.2026 в дневном файле оказалось 143 строки-повтора, а по всей
  истории — 1311 повторов на 1288 уникальных сделок, то есть почти половина.
  `scripts/pos.py` дедуплицирует сам и потому не врал, но всякий, кто просто
  суммирует журнал, считает вдвое;

* **записи книги умных заявок на ИСТЁКШИХ контрактах.** Сторож перебирает книгу
  на каждом проходе, несколько раз в секунду; 02.10.2026 из 276 записей 129 были
  на RIU6, истёкшем 18.09, и ни одна не живая.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО: удаления. Торговая история — это аудит, её переносят в
архив, а не стирают. Дубль — исключение: это не история, а одна и та же сделка,
записанная дважды, и вторая запись не несёт ничего.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess

TRADES_GLOB = "data/trades/*.jsonl"
BOOK = "data/smart_orders.json"
# Контракты, истёкшие к октябрю 2026. Список ЯВНЫЙ: вычислять экспирацию из кода
# инструмента значит угадывать, а ошибка здесь уносит в архив живую заявку.
DEAD_CODES = {"RIU6", "BRU6", "SiU6", "GDU6", "RIM6", "BRM6", "SiM6", "GDM6"}
LIVE_STATUSES = {"armed", "native", "closing"}


def _backup(path: str) -> str:
    dst = f"{path}.bak-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def dedup_trades(apply: bool) -> int:
    """Убрать из дневного файла строки, которые уже есть в БОЛЕЕ РАННЕМ дне.

    Первое вхождение остаётся всегда: именно оно описывает сделку, а повтор в
    следующем файле — артефакт датировки QUIK. Сравнение по номеру сделки (`num`),
    он у QUIK уникален; строка без номера не трогается, потому что опознать её
    нечем.
    """
    files = sorted(glob.glob(TRADES_GLOB))
    seen: set[str] = set()
    removed_total = 0
    for path in files:
        kept, removed = [], 0
        size_before = os.path.getsize(path)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    num = str(json.loads(line).get("num") or "")
                except Exception:        # noqa: BLE001 — битую строку не теряем
                    kept.append(line)
                    continue
                if num and num in seen:
                    removed += 1
                    continue
                if num:
                    seen.add(num)
                kept.append(line)
        if not removed:
            continue
        removed_total += removed
        print(f"  {path}: повторов {removed}, останется {len(kept)}")
        if not apply:
            continue
        # ФАЙЛ МОГ ВЫРАСТИ, пока мы его читали: сторож пишет в него на ходу.
        # Перезапись вслепую потеряла бы свежие сделки — это дороже любого дубля.
        if os.path.getsize(path) != size_before:
            print(f"  {path}: ПРОПУЩЕН — файл изменился во время чтения, "
                  "повторите уборку, когда торги закрыты")
            continue
        _backup(path)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        os.replace(tmp, path)
    return removed_total


def _service_running(name: str = "shectory-trader") -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", name], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() == "active"
    except Exception:          # noqa: BLE001 — не смогли спросить, считаем «работает»
        return True


def archive_book(apply: bool) -> int:
    """Записи на истёкших контрактах и в терминальных статусах — в архив.

    Правится ТОЛЬКО при остановленной службе: процесс держит книгу в памяти и
    перезапишет файл своим состоянием на первом же сохранении.
    """
    if not os.path.exists(BOOK):
        return 0
    book = json.load(open(BOOK, encoding="utf-8"))
    wrap = isinstance(book, dict)
    orders = book["orders"] if wrap else book
    arch = [o for o in orders
            if o.get("code") in DEAD_CODES and o.get("status") not in LIVE_STATUSES]
    if not arch:
        return 0
    print(f"  книга: {len(orders)} записей, в архив {len(arch)} "
          f"(истёкшие контракты, не живые)")
    if not apply:
        return len(arch)
    if _service_running():
        print("  книга: ПРОПУЩЕНА — служба shectory-trader работает и перезапишет "
              "файл. Остановите её на время уборки.")
        return 0
    keep = [o for o in orders if o not in arch]
    assert len(keep) + len(arch) == len(orders), "потеряли записи — не пишу"
    assert all(o.get("status") not in LIVE_STATUSES for o in arch), "живое в архиве"
    _backup(BOOK)
    ap = f"data/smart_orders_archive_{datetime.date.today().isoformat()}.json"
    old = json.load(open(ap, encoding="utf-8")) if os.path.exists(ap) else []
    json.dump(old + arch, open(ap, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if wrap:
        book["orders"] = keep
    else:
        book = keep
    json.dump(book, open(BOOK, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  книга: перезаписана, архив {ap} ({len(old) + len(arch)} записей)")
    return len(arch)


def main() -> int:
    ap = argparse.ArgumentParser(description="Уборка после торгового дня")
    ap.add_argument("--apply", action="store_true",
                    help="править файлы (без него только показывает)")
    ap.add_argument("--skip-book", action="store_true",
                    help="не трогать книгу умных заявок")
    args = ap.parse_args()
    print("=== дубли в журнале сделок ===")
    dups = dedup_trades(args.apply)
    print(f"повторов {'убрано' if args.apply else 'найдено'}: {dups}")
    booked = 0
    if not args.skip_book:
        print("=== книга умных заявок ===")
        booked = archive_book(args.apply)
    print(f"ИТОГ: журнал {dups}, книга {booked}, "
          f"режим {'ПРАВКА' if args.apply else 'показ'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
