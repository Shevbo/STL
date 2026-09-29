"""Выжимка ленты сделок за день: без урезания, одна сделка архива -> одна строка.

ЗАЧЕМ. Гипотезы D1-D3 реестра (`docs/algo-footprints-registry.md`: нарезка
TWAP/VWAP, крупный принт, подписанный поток OFI) считают по КАЖДОЙ сделке —
минутные бары стирают именно то, что ищут. Лента лежит только на хостере
(`trade-<дата>.jsonl`, на момент написания только 25.09), единственный канал к i9
— `agent_bars/<ключ>.json`.

СТОРОНА. side=2 значит покупка (агрессор-покупатель), side=1 — продажа. Источник
истины — `quik_agent/lua/shectory_trade.lua`, `OnAllTrade`: бит 0x1 флагов QUIK =
sell aggressor -> side=1, бит 0x2 = buy aggressor -> side=2. Go-агент
(`quik_agent/cmd/quik-agent/main.go`, `Side: int32(r[2])`) и
`trader/quik/recorder.py` (`_write_tape`, `"side": int(t.get("side") or 0)`)
прокидывают это число НЕИЗМЕНЁННЫМ до архива — ни один слой не переворачивает
знак. Комментарий в `proto/shectory/quik/v1/quik_agent.proto` у `TapeTrade.side`
("1 = покупка, 2 = продажа") этому коду противоречит; доверяем коду, который
реально вычисляет значение, а не устаревшей подписи. `side_buy` кладём в выдачу
явно, чтобы потребителю не пришлось перепроверять это самому.

ВРЕМЯ. Тот же сдвиг +3 ч (MSK_SHIFT_MS), что у `book_digest.py` /
`book_full_digest.py`: архив в UTC, бары агента — московская стенка, подписанная
как UTC. Источник метки — поле `ts_ms` строки архива (штамп ПРИЁМА агентом, лента
QUIK времени сделки в OnAllTrade не даёт — см. `recorder.py`).

    python scripts/tape_digest.py --code RIZ6 --date 2026-09-25 \
        --out agent_bars/tapeRIZ6d0925.json [--archive ~/market-archive]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import datetime, timezone

MSK_SHIFT_MS = 3 * 3600 * 1000
SIDE_BUY = 2  # см. докстринг: 0x2 = buy aggressor в shectory_trade.lua, OnAllTrade
DEFAULT_ARCHIVE = "~/market-archive"


def _pick_file(archive: str, date: str) -> str | None:
    """Ровно один файл на день. "(.prev|.gz)" в имени — варианты ОДНОГО дня, не
    разные дни, конкатенировать их нельзя.

    Найдено на архиве 25.09: `.gz` (260424 строки, `source=quik_export`) и `.prev`
    (259382 строки) существуют одновременно — `.prev` короче и обрывается на ~13
    минут раньше по времени последней сделки. `.gz` мтайм на следующий день в
    03:00 — штатная ночная ротация, как у всех `book-*.jsonl.gz`; `.prev` датирован
    тем же днём 23:51 — снят ДО ротации, видимо при починке остановленного потока
    (см. `docs/algo-footprints-registry.md`: «поток встал, чинит real-trade»).
    `.gz` — итоговый полный файл дня, `.prev` — более ранняя неполная версия.
    Прочитать оба подряд задвоило бы общую часть (259382 строки). Приоритет: живой
    `.jsonl` (день ещё не заротирован) > `.jsonl.gz` (штатная ротация, полный день)
    > `.jsonl.prev` (запасной вариант, если ротации не было вовсе).
    """
    for suffix in ("", ".gz", ".prev"):
        p = os.path.join(archive, f"trade-{date}.jsonl{suffix}")
        if os.path.exists(p):
            return p
    return None


def build(archive: str, code: str, date: str) -> dict:
    rows = []
    path = _pick_file(archive, date)
    if path is not None:
        needle = f'"{code}"'
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if needle not in ln:
                    continue
                try:
                    r = json.loads(ln)
                    if r.get("code") != code:
                        continue
                    ts_ms = int(r["ts_ms"]) + MSK_SHIFT_MS
                    price = float(r["price"])
                    qty = int(r["qty"])
                    side = int(r["side"])
                except (KeyError, TypeError, ValueError):
                    continue
                if price <= 0 or qty <= 0:
                    continue
                rows.append([ts_ms, price, qty, side])
    rows.sort(key=lambda r: r[0])
    return {"key": f"tape{code}", "code": code, "ts_unit": "ms", "side_buy": SIDE_BUY,
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_base": "bars (+3h от UTC архива)", "rows": rows, "source_file":
                os.path.basename(path) if path else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE)
    a = ap.parse_args()

    data = build(os.path.expanduser(a.archive), a.code, a.date)
    rows = data["rows"]
    if not rows:
        raise SystemExit(f"нет сделок {a.code} за {a.date} в архиве")
    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    size = os.path.getsize(out) / 1e6
    print(f"{a.code} {a.date}: сделок {len(rows)}, файл {size:.1f} МБ -> {out} "
          f"(источник {data['source_file']})")
    print(f"  первая {rows[0][0]}, последняя {rows[-1][0]}, side_buy={SIDE_BUY}")


if __name__ == "__main__":
    main()
