"""Выжимка архивного стакана на ПОЛНОЙ частоте снимков (без прореживания до минуты).

ЗАЧЕМ. `book_digest.py` даёт 1 снимок/мин — годится для баров, но гипотезы C1-C4
реестра (`docs/algo-footprints-registry.md`: дисбаланс стакана/microprice, айсберги,
спуфинг/layering, отвод ликвидности перед ходом) живут и умирают за секунды —
ниже разрешения минутного бара. Им нужна ЧАСТОТА снимков архива (~1/1.2 с), не
минутная нарезка.

ЧТО ВНУТРИ. Формат строки строки-снимка как у `book_digest.py` (5 уровней bid, 5
уровней ask, добивка короткой стороны худшей ценой стороны с нулевым объёмом — та
же причина: `_walk` потребителя читает стороны по фиксированному смещению
2*LEVELS), но БЕЗ прореживания: КАЖДЫЙ валидный снимок кода за окно — своя строка.
Время — тот же сдвиг +3 ч (MSK_SHIFT), что и у `book_digest.py`, но в
МИЛЛИСЕКУНДАХ (`ts_ms`), не в секундах: на суб-секундной частоте секундная сетка
схлопывает разные снимки в один. `"ts_unit":"ms"` в результате — явная метка для
потребителя, что формат времени здесь не тот же, что у `book_digest.py`.

ПАМЯТЬ. Хостер слабый: чтение построчное (`gzip.open`, генератор), и ВЫВОД тоже
построчный — строки JSON-массива пишутся в файл по мере чтения, без накопления
всех снимков в памяти (за 13 дней на полной частоте это сотни тысяч строк).

    python scripts/book_full_digest.py --code RIZ6 --from 2026-09-16 --to 2026-09-29 \
        --out agent_bars/bookRIZ6f0929.json [--archive ~/market-archive] [--thin 2]

`--thin N` (по умолчанию 1) оставляет каждый N-й валидный снимок — прореженный
вдвое вариант при переросшем файле делается тем же скриптом с `--thin 2`.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import glob
import gzip
import json
import os
from datetime import datetime, timezone

MSK_SHIFT_MS = 3 * 3600 * 1000
LEVELS = 5
DEFAULT_ARCHIVE = "~/market-archive"


def merge_windows(anchors_path: str, before_s: float, after_s: float) -> tuple[list[list[int]], int]:
    """Слитые окна [t-before, t+after] в мс шкалы выжимки (ts якорей уже в ней)."""
    with open(anchors_path, encoding="utf-8") as f:
        ts = sorted(r[0] for r in json.load(f)["rows"])
    wins: list[list[int]] = []
    for t in ts:
        lo, hi = t - int(before_s * 1000), t + int(after_s * 1000)
        if wins and lo <= wins[-1][1]:
            wins[-1][1] = max(wins[-1][1], hi)
        else:
            wins.append([lo, hi])
    return wins, len(ts)


def _rows(archive: str, code: str, d_from: str | None, d_to: str | None, thin: int,
          wins: list[list[int]] | None = None):
    """Генератор валидных строк-снимков в хронологическом порядке файлов.

    wins: слитые отсортированные окна, память O(окон).
    """
    starts = [w[0] for w in wins] if wins else []
    files = sorted(glob.glob(os.path.join(archive, "book-*.jsonl*")))
    needle = f'"{code}"'
    kept = 0
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
                    ts_ms = int(r["received_at_unix_ms"]) + MSK_SHIFT_MS
                except (KeyError, TypeError, ValueError):
                    continue
                if wins is not None:
                    # bisect, а не курсор: -recovered и основной файл дня могут перекрываться по времени
                    i = bisect_right(starts, ts_ms) - 1
                    if i < 0 or ts_ms > wins[i][1]:
                        continue
                kept += 1
                if (kept - 1) % thin:
                    continue
                row = [ts_ms]
                for side in (bids[:LEVELS], asks[:LEVELS]):
                    for lvl in side:
                        row.append(float(lvl["price"]))
                        row.append(int(lvl["quantity"]))
                    # ДОБИВКА ДО LEVELS — см. book_digest.py.
                    for _ in range(LEVELS - len(side)):
                        row.append(float(side[-1]["price"]))
                        row.append(0)
                yield row


def build(archive: str, code: str, d_from: str | None, d_to: str | None,
          out_path: str, thin: int, around: dict | None = None) -> tuple[int, int | None, int | None]:
    """Пишет результат потоково в out_path (во временный файл, атомарная замена).

    Возвращает (число строк, первый ts_ms, последний ts_ms).
    """
    tmp = out_path + ".tmp"
    wins = None
    extra = ""
    if around:
        wins, na = merge_windows(around["path"], around["before_s"], around["after_s"])
        extra = '"around":{"anchors":%d,"before_s":%s,"after_s":%s},' % (
            na, around["before_s"], around["after_s"])
    n = 0
    first_ts = last_ts = None
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as out:
        out.write(
            '{"key":"book%s","code":"%s","levels":%d,"ts_unit":"ms","built":"%s",'
            '"time_base":"bars (+3h от UTC архива)",%s"rows":[' % (
                code, code, LEVELS,
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), extra))
        for row in _rows(archive, code, d_from, d_to, thin, wins):
            if n:
                out.write(",")
            out.write(json.dumps(row, separators=(",", ":")))
            if first_ts is None:
                first_ts = row[0]
            last_ts = row[0]
            n += 1
        out.write("]}")
    if n == 0:
        os.remove(tmp)
        raise SystemExit(f"нет снимков {code} в архиве за окно")
    os.replace(tmp, out_path)
    return n, first_ts, last_ts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE)
    ap.add_argument("--thin", type=int, default=1,
                     help="оставить каждый N-й снимок (2 = прореженный вдвое)")
    ap.add_argument("--around", help="anchors.json: только снимки в окнах вокруг якорей")
    ap.add_argument("--before-s", type=float, default=10)
    ap.add_argument("--after-s", type=float, default=420)
    a = ap.parse_args()
    around = {"path": a.around, "before_s": a.before_s, "after_s": a.after_s} if a.around else None

    out = os.path.expanduser(a.out)
    n, first_ts, last_ts = build(os.path.expanduser(a.archive), a.code,
                                  a.d_from, a.d_to, out, max(1, a.thin), around)
    size = os.path.getsize(out) / 1e6
    print(f"{a.code}: снимков {n}, файл {size:.1f} МБ -> {out} (thin={a.thin})")
    # ВЕРСИЯ В ИМЕНИ ОБЯЗАТЕЛЬНА — см. book_digest.py: агент кэширует выжимку по
    # ключу, тот же файл под тем же именем не перекачивается.
    print("  ключ для задания = имя файла без .json; меняешь содержимое — меняй имя")
    print(f"  первый {first_ts}, последний {last_ts}, уровней {LEVELS}, ts_unit=ms")


if __name__ == "__main__":
    main()
