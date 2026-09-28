"""Что РЕАЛЬНО собирается в архив стакана: состав инструментов и интервал.

ЗАЧЕМ. 28.09.2026 оператор считал, что на торговой машине работает конфиг с
одним инструментом и снимком раз в пять секунд. По архиву шли ЧЕТЫРЕ инструмента
раз в 1.15 секунды. Проверить версию работающего скрипта нельзя: Lua пишет только
в окно QUIK, SSH на машину нет ни у кого из окон разработки. Единственный
наблюдаемый факт — сами данные, и мерить надо по ним.

ЗАЧЕМ ЭТО ВАЖНО, а не любопытно. 25.09.2026 шесть подписок раз в секунду довели
QUIK до того, что он перестал отвечать серверу доступа: сессия рвалась каждую
минуту, потеряно восемь торговых часов архива. Нагрузка = снимки в секунду, а не
число инструментов: четыре инструмента раз в пять секунд дают 0.8 снимка в
секунду и это БЕЗОПАСНЕЕ, чем один инструмент раз в секунду.

Опорные числа для сравнения (померены по этим же архивам):
  24.09, шесть подписок, день перед сбоем — 4.91 снимка/с, 1.22 с на поток;
  28.09, до перезапуска скрипта       — 3.47 снимка/с, 1.15 с на поток (71%).

    python scripts/book_load_check.py                 # сегодняшний архив
    python scripts/book_load_check.py --date 2026-09-24 --minutes 0
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import json
import os

ARCHIVE = os.environ.get("STL_ARCHIVE", "/home/ubuntu/market-archive")
BROKE_RATE = 4.91          # снимков/с в конфигурации, сломавшей терминал 25.09.2026


def scan(path: str, minutes: int) -> tuple[collections.Counter, float, float]:
    """Состав и окно наблюдения. minutes>0 — только хвост архива."""
    op = gzip.open if path.endswith(".gz") else open
    cnt: collections.Counter = collections.Counter()
    t0 = t1 = None
    cutoff = None
    if minutes > 0:
        cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - minutes * 60
    with op(path, "rt") as f:
        for ln in f:
            try:
                d = json.loads(ln)
            except Exception:  # noqa: BLE001 — битая строка не повод падать
                continue
            t = d.get("received_at_unix_ms") or d.get("stl_recv_ms")
            v = float(t) / 1000.0 if t else None
            if cutoff and (v is None or v < cutoff):
                continue
            cnt[d.get("code") or d.get("sec") or "?"] += 1
            if v:
                t0 = v if t0 is None else min(t0, v)
                t1 = v if t1 is None else max(t1, v)
    return cnt, (t0 or 0), (t1 or 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--minutes", type=int, default=30,
                    help="считать только последние N минут (0 = весь файл)")
    a = ap.parse_args()

    base = f"{ARCHIVE}/book-{a.date}.jsonl"
    path = base if os.path.exists(base) else base + ".gz"
    if not os.path.exists(path):
        raise SystemExit(f"нет архива стакана за {a.date}")

    cnt, t0, t1 = scan(path, a.minutes)
    total = sum(cnt.values())
    span = t1 - t0
    if not total or span <= 0:
        raise SystemExit("в выбранном окне снимков нет — стакан не идёт")
    rate = total / span
    per = rate / len(cnt)
    print(f"архив {os.path.basename(path)}, окно {span / 60:.1f} мин")
    print(f"инструментов {len(cnt)}: {', '.join(sorted(cnt))}")
    print(f"ВСЕГО {rate:.2f} снимков/с | на инструмент {per:.2f}/с "
          f"= интервал {1 / per:.2f} с")
    print(f"доля от нагрузки, сломавшей терминал 25.09: {100 * rate / BROKE_RATE:.0f}%")
    for s, c in cnt.most_common():
        print(f"   {s:8} {c:>7} снимков")
    if rate > 0.5 * BROKE_RATE:
        print("\nВНИМАНИЕ: больше половины сломавшей нагрузки. Интервал в конфиге "
              "не действует, пока скрипт не перезапущен.")


if __name__ == "__main__":
    main()
