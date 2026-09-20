"""Выжимка архивного стакана: 100 МБ сырых снимков -> файл, который увозит i9.

ЗАЧЕМ. Прогон по стакану жил только там, где лежит архив (хостер) или его копия.
Перебор идёт на i9, а туда ходит ровно один канал данных — ручка
`/api/v1/agent/bars/<key>`, которая отдаёт файл `agent_bars/<key>.json`. Значит,
чтобы научить i9 считать по стакану, стакан надо привести к такому же файлу:
никаких новых эндпоинтов и никакой чужой зоны.

ЧТО ВНУТРИ. Один снимок на МИНУТУ — последний в минуте, 5 уровней с каждой
стороны. Этого достаточно, потому что стратегия принимает решение на закрытии
бара, а BookRuntime ищет книгу не старше `max_gap_s` (по умолчанию 60 с). Десять
уровней архива режутся до пяти: наши объёмы 1-30 контрактов, а пятого уровня RI
хватает с запасом (замер глубины 16.09.2026: на лучшем уровне медианно 4
контракта, 20 лотов стоят 14.5 пт).

ВРЕМЯ. Метки приводятся к шкале баров сразу (+3 ч, MSK_SHIFT): бары ISS и бары
агента — московская стенка, проставленная как UTC, архив — настоящий UTC.
Проверено 20.09.2026 на RIU6: при сдвиге +3 ч медиана |close − mid| = 5 пт
(ровно полспреда), при 0 ч — 425 пт.

    python scripts/book_digest.py --archive ~/market-archive --code RIU6 \
        --out agent_bars/bookRIU6.json [--from 2026-08-12] [--to 2026-09-20]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os

MSK_SHIFT = 3 * 3600
LEVELS = 5


def build(archive: str, code: str, d_from: str | None, d_to: str | None) -> dict:
    per_minute: dict[int, list] = {}
    files = sorted(glob.glob(os.path.join(archive, "book-*.jsonl*")))
    needle = f'"{code}"'
    for path in files:
        day = os.path.basename(path)[5:15]          # book-YYYY-MM-DD...
        if (d_from and day < d_from) or (d_to and day > d_to):
            continue
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if needle not in ln:
                    continue
                try:
                    r = json.loads(ln)
                    if r.get("code") != code:
                        continue
                    bids = r.get("bids") or []
                    asks = r.get("asks") or []
                    if not bids or not asks:
                        continue
                    ts = int(r["received_at_unix_ms"]) // 1000 + MSK_SHIFT
                except (KeyError, TypeError, ValueError):
                    continue
                row = [ts - ts % 60]
                for side in (bids[:LEVELS], asks[:LEVELS]):
                    for lvl in side:
                        row.append(float(lvl["price"]))
                        row.append(int(lvl["quantity"]))
                # Последний снимок минуты побеждает: он ближе всего к закрытию бара.
                per_minute[row[0]] = row
    rows = [per_minute[k] for k in sorted(per_minute)]
    return {"key": f"book{code}", "code": code, "levels": LEVELS,
            "time_base": "bars (+3h от UTC архива)", "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to")
    a = ap.parse_args()

    data = build(os.path.expanduser(a.archive), a.code, a.d_from, a.d_to)
    rows = data["rows"]
    if not rows:
        raise SystemExit(f"нет снимков {a.code} в архиве за окно")
    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    size = os.path.getsize(out) / 1e6
    print(f"{a.code}: минут {len(rows)}, файл {size:.1f} МБ -> {out}")
    print(f"  первая {rows[0][0]}, последняя {rows[-1][0]}, "
          f"уровней {LEVELS}, спред первой минуты "
          f"{rows[0][1 + 2 * LEVELS] - rows[0][1]:.0f} пт")


if __name__ == "__main__":
    main()
