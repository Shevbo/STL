"""Профиль спреда из архива: сколько стоит рыночная заявка по часам и по инструментам.

Спред — свойство рынка, а не стратегии: одна средняя цифра (10 пт на филл на RIU6)
может оказаться хвостом вечерки или последней недели перед экспирацией. Тогда это
лечится расписанием, а не константой издержек (замечание fable 16.09.2026).

Считает полспреда = (ask − bid) / 2:
  из стакана (book-*), по лучшим уровням — для инструмента с записанным стаканом;
  из котировок (tick-*), где есть bid и ask — для всех остальных.
Время МСК (архив в UTC, +3 ч).

    PYTHONPATH=. python scripts/spread_profile.py --dir book/ --kind book --code RIU6
    PYTHONPATH=. python scripts/spread_profile.py --dir ticks/ --kind tick
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

MSK_SHIFT = 3 * 3600


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def rows(path: str):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        for ln in f:
            try:
                yield json.loads(ln)
            except ValueError:
                continue


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--kind", choices=("book", "tick"), default="book")
    ap.add_argument("--code", help="только этот инструмент")
    a = ap.parse_args()

    by_hour: dict[tuple[str, int], list[float]] = defaultdict(list)
    by_day: dict[tuple[str, str], list[float]] = defaultdict(list)
    for name in sorted(os.listdir(a.dir)):
        if not name.startswith(a.kind + "-"):
            continue
        for r in rows(os.path.join(a.dir, name)):
            code = r.get("code")
            if not code or (a.code and code != a.code):
                continue
            if a.kind == "book":
                bids, asks = r.get("bids") or [], r.get("asks") or []
                if not bids or not asks:
                    continue
                bid, ask = max(float(x["price"]) for x in bids), min(float(x["price"]) for x in asks)
            else:
                bid, ask = r.get("bid"), r.get("ask")
                if not bid or not ask:
                    continue
            half = (float(ask) - float(bid)) / 2
            if half <= 0:                      # cross/lock: помечены в архиве, в профиль не идут
                continue
            ts = int(r["received_at_unix_ms"]) // 1000 + MSK_SHIFT
            d = datetime.fromtimestamp(ts, timezone.utc)
            by_hour[(code, d.hour)].append(half)
            by_day[(code, d.strftime("%Y-%m-%d"))].append(half)

    for code in sorted({c for c, _ in by_hour}):
        allv = [v for (c, _), xs in by_hour.items() if c == code for v in xs]
        print(f"\n=== {code}: снимков {len(allv)}, полспреда пт — медиана {median(allv):.1f}, "
              f"среднее {sum(allv) / len(allv):.1f}, p90 {pct(allv, 0.9):.1f}, p99 {pct(allv, 0.99):.1f}")
        print("  час МСК:", " ".join(f"{h:02d}:{median(by_hour[(code, h)]):.0f}"
                                     for h in sorted(h for c, h in by_hour if c == code)))
        days = sorted(d for c, d in by_day if c == code)
        tail = days[-7:]
        print("  последние дни:", " ".join(f"{d[5:]}:{median(by_day[(code, d)]):.0f}" for d in tail))


if __name__ == "__main__":
    main()
