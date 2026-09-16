"""Во что превращается заявка размером больше одного лота (16.09.2026).

Все замеры исполнения до сих пор делались на 1 лоте, и глубины всегда хватало. Живой
шорт-лист оператора — это 12 ячеек на RI, то есть до 12 лотов в одну сторону. Скрипт
считает по архивному стакану, сколько стоит рыночная заявка на 1/5/10/12/20 лотов:
средняя цена против лучшей котировки, в пунктах, медиана и p90, по часам МСК.

    PYTHONPATH=. python scripts/book_depth.py --dir book/ --code RIU6
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
SIZES = (1, 5, 10, 12, 20)


def walk(levels: list[tuple], qty: int) -> tuple[float, bool]:
    left, cost = qty, 0.0
    for price, vol in levels:
        take = min(left, vol)
        cost += take * price
        left -= take
        if left == 0:
            return cost / qty, False
    cost += left * levels[-1][0]
    return cost / qty, True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--code", default="RIU6")
    ap.add_argument("--every", type=int, default=10, help="брать каждый N-й снимок")
    a = ap.parse_args()

    cost = {q: [] for q in SIZES}
    deep = {q: 0 for q in SIZES}
    by_hour = {q: defaultdict(list) for q in SIZES}
    top_vol, n = [], 0
    for name in sorted(os.listdir(a.dir)):
        if not name.startswith("book-"):
            continue
        op = gzip.open if name.endswith(".gz") else open
        with op(os.path.join(a.dir, name), "rt", encoding="utf-8") as f:
            for i, ln in enumerate(f):
                if i % a.every or a.code not in ln:
                    continue
                try:
                    r = json.loads(ln)
                    if r.get("code") != a.code:
                        continue
                    asks = sorted((float(x["price"]), int(x["quantity"])) for x in r["asks"])
                    bids = sorted(((float(x["price"]), int(x["quantity"])) for x in r["bids"]), reverse=True)
                except (KeyError, TypeError, ValueError):
                    continue
                if not asks or not bids:
                    continue
                h = datetime.fromtimestamp(int(r["received_at_unix_ms"]) // 1000 + MSK_SHIFT,
                                           timezone.utc).hour
                mid = (bids[0][0] + asks[0][0]) / 2
                top_vol.append(min(asks[0][1], bids[0][1]))
                n += 1
                for q in SIZES:
                    buy, d1 = walk(asks, q)
                    sell, d2 = walk(bids, q)
                    c = ((buy - mid) + (mid - sell)) / 2      # средняя цена обеих сторон
                    cost[q].append(c)
                    by_hour[q][h].append(c)
                    deep[q] += d1 or d2

    print(f"{a.code}: снимков {n} (каждый {a.every}-й), объём на лучшем уровне — медиана "
          f"{median(top_vol):.0f} контрактов")
    print(f"{'лотов':>6} {'медиана пт':>11} {'p90':>7} {'p99':>7} {'глубины не хватило':>20}")
    for q in SIZES:
        s = sorted(cost[q])
        print(f"{q:6} {median(s):11.1f} {s[int(0.9 * len(s))]:7.1f} {s[int(0.99 * len(s))]:7.1f} "
              f"{deep[q] / max(n, 1):19.0%}")
    print("\nмедиана по часам МСК (пункты на контракт):")
    hours = sorted(by_hour[1])
    print("   час:  " + " ".join(f"{h:5d}" for h in hours))
    for q in SIZES:
        print(f"{q:5} лот: " + " ".join(f"{median(by_hour[q][h]):5.0f}" for h in hours))


if __name__ == "__main__":
    main()
