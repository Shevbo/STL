"""Склейка RI.txt -> поконтрактные файлы баров для i9.

ЗАЧЕМ. Считает только i9, а туда ведёт один канал данных: `/api/v1/agent/bars/<key>`
отдаёт `agent_bars/<key>.json`. Чтобы судить ось на ИСТОРИИ, а не на одном окне,
нужны бары каждого квартального контракта отдельно — склейка через экспирацию даёт
ложные скачки цены на роллах, а поконтрактный прогон их не знает вовсе.

Ключ = код контракта (RIH2, RIM2, ...), формат ровно как у существующих файлов:
{"key": ..., "rows": [[time, open, high, low, close, volume], ...]}. Время — метка из
склейки (московская стенка, проставленная как UTC), как во всех барах лаборатории.

    python scripts/publish_contract_bars.py ~/stl_fetch/RI.txt --out agent_bars \
        [--min-bars 5000] [--prefix ""]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("splice")
    ap.add_argument("--out", default="agent_bars")
    ap.add_argument("--min-bars", type=int, default=5000)
    # ПРЕФИКС ОБЯЗАТЕЛЕН ПО УМОЛЧАНИЮ. 21.09.2026 запуск без него перезаписал файлы
    # прогрева ЖИВЫХ роботов: agent_bars/<КОД>.json пишут rf_fetch_contracts и cron
    # warm_robot_bars, оттуда робот берёт историю при выкладке. RIZ6 подменился
    # складом на 4 тыс баров короче и на двое суток старее; восстанавливал вручную.
    ap.add_argument("--prefix", default="sp", help="префикс ключа (пустой = опасно)")
    ap.add_argument("--force", action="store_true",
                    help="перезаписывать существующие файлы")
    a = ap.parse_args()

    by: dict[str, list] = defaultdict(list)
    with open(os.path.expanduser(a.splice), encoding="utf-8") as f:
        next(f)                                   # заголовок Финам/TSLab
        for ln in f:
            t = ln.rstrip().split(",")
            if len(t) < 10:
                continue
            ts = int(datetime.strptime(t[2] + t[3], "%Y%m%d%H%M%S")
                     .replace(tzinfo=timezone.utc).timestamp())
            by[t[9]].append([ts, float(t[4]), float(t[5]), float(t[6]),
                             float(t[7]), int(t[8])])

    os.makedirs(os.path.expanduser(a.out), exist_ok=True)
    written = 0
    for code, rows in sorted(by.items()):
        if len(rows) < a.min_bars:
            print(f"  {code}: {len(rows)} баров — мало, пропускаю")
            continue
        rows.sort(key=lambda r: r[0])
        key = f"{a.prefix}{code}"
        path = os.path.join(os.path.expanduser(a.out), f"{key}.json")
        if os.path.exists(path) and not a.force:
            print(f"  {key}: файл уже есть — пропускаю (--force чтобы заменить)")
            continue
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "rows": rows}, fh, separators=(",", ":"))
        written += 1
        print(f"  {key}: {len(rows)} баров, {os.path.getsize(path) / 1e6:.1f} МБ")
    print(f"готово: {written} контрактов")


if __name__ == "__main__":
    main()
