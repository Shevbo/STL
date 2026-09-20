"""Выжимка архивного стакана: 100 МБ сырых снимков -> файл, который увозит i9.

ЗАЧЕМ. Прогон по стакану жил только там, где лежит архив (хостер) или его копия.
Перебор идёт на i9, а туда ходит ровно один канал данных — ручка
`/api/v1/agent/bars/<key>`, которая отдаёт файл `agent_bars/<key>.json`. Значит,
чтобы научить i9 считать по стакану, стакан надо привести к такому же файлу:
никаких новых эндпоинтов и никакой чужой зоны.

ЧТО ВНУТРИ. Один снимок на МИНУТУ — ПЕРВЫЙ в минуте, с настоящим временем, 5
уровней с каждой стороны. Первый, а не последний: BookRuntime спрашивает книгу на
открытии следующего бара, то есть на границе минуты, и снимок из середины минуты,
подписанный границей, подмешал бы в цену исполнения снос рынка (и заглядывание
вперёд). Порог свежести `max_gap_s` (60 с) отсекает дыры архива. Десять
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
from datetime import datetime, timezone

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
                row = [ts]
                for side in (bids[:LEVELS], asks[:LEVELS]):
                    for lvl in side:
                        row.append(float(lvl["price"]))
                        row.append(int(lvl["quantity"]))
                    # ДОБИВКА ДО LEVELS. load_digest читает стороны по фиксированному
                    # смещению 2*LEVELS; сторона короче пяти уровней сдвигала аски в
                    # биды: sell исполнялся по ask (лучше рынка), buy начинал с 3-4-го
                    # уровня. Пустой уровень = худшая цена стороны с нулевым объёмом,
                    # _walk его пропускает. Найдено 20.09.2026: 1 минута из 29110 RIU6.
                    for _ in range(LEVELS - len(side)):
                        row.append(float(side[-1]["price"]))
                        row.append(0)
                # ПЕРВЫЙ снимок минуты, и время у него НАСТОЯЩЕЕ. BookRuntime ищет
                # книгу не раньше открытия следующего бара, то есть на границе
                # минуты; если оставить последний снимок и подписать его границей,
                # заявка исполнится по книге, которая была на 59 секунд позже, —
                # это снос цены, записанный в цену исполнения (и заглядывание
                # вперёд). Найдено 20.09.2026 на первом же прогоне пар.
                key = ts - ts % 60
                if key not in per_minute or ts < per_minute[key][0]:
                    per_minute[key] = row
    rows = [per_minute[k] for k in sorted(per_minute)]
    return {"key": f"book{code}", "code": code, "levels": LEVELS,
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    # ВЕРСИЯ В ИМЕНИ ОБЯЗАТЕЛЬНА. Агент кэширует выжимку по ключу и при том же
    # имени НЕ перекачивает файл: 20.09.2026 пересобранная выжимка с исправленным
    # временем дала результаты бит-в-бит прежние — считался старый файл из памяти.
    print("  ключ для задания = имя файла без .json; меняешь содержимое — меняй имя")
    print(f"  первая {rows[0][0]}, последняя {rows[-1][0]}, "
          f"уровней {LEVELS}, спред первой минуты "
          f"{rows[0][1 + 2 * LEVELS] - rows[0][1]:.0f} пт")


if __name__ == "__main__":
    main()
