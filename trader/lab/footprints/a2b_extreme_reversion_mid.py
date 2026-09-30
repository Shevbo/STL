"""A2b: тот же откат после нового экстремума, что и A2, но цена — mid
стакана, где нет bid/ask-отскока close.

Реестр: docs/algo-footprints-registry.md, строка A2. A2 на close баров M1
нашёл откат после нового N-минутного экстремума (RIZ6 N=20 h=15: медиана
-20 пт, обе половины, CI без нуля); часть этого может быть механическим
bid/ask-отскоком close (тик RI 10 пт, полспреда 5) — та же ловушка, что уже
потребовала проверки B1 на mid (реестр, строка B1). Здесь тот же вопрос
меряется на цене без отскока: mid = (bid1+ask1)/2 из полной выжимки стакана.

ДАННЫЕ. Как у C1 (`c1_book_imbalance._load_full_book`, ключ вида
`book<КОД>fMMDD`, ~1 снимок/1.2 с, 5 уровней) — НЕ поминутная выжимка
`book_digest.py`, которую грузит `common.load_book`. Из снимков строятся
минутные бары mid [ts_начала_минуты_сек, open, high, low, close,
n_snapshots] (объём тут = число снимков, справочно, не сравним с объёмом
сделок), дальше без изменений в `a2.analyze` — тот же алгоритм пробоя/отката,
те же контроли и halves, что у A2 на close.

ПРОПУСКИ. Минута без единого снимка просто отсутствует в барах (пропуск, не
заполнение). Минута считается ЗАТРОНУТОЙ дырой, если разрыв между какими-то
двумя ПОДРЯД идущими снимками полного ряда превышает MAX_GAP_S, а интервал
этого разрыва задевает границы данной минуты — так помечаются ОБЕ минуты по
краям дыры, даже если одна из них успела накопить часть своих секунд.
ponytail: решение намеренно консервативное (можно точнее отделять «дыра
пришла в хвост минуты» от «дыра пришла в начало»), но здесь цена гадать на
пропуске — фантомный экстремум, тот же урок, что reference_splice_phantom_rounds
и reference_synthetic_bars_tail; апгрейд — если на реальных данных так
выбрасывается заметная доля минут.
"""
from __future__ import annotations

import statistics

from trader.lab.footprints import a2_channel_breakout as a2
from trader.lab.footprints import c1_book_imbalance as c1
from trader.lab.footprints import common

MAX_GAP_S = 60


def _minute_bars_mid(rows: list[tuple]) -> tuple[list[list], int]:
    """rows = [(ts_ms, bids, asks), ...] (см. c1._load_full_book) ->
    (бары [ts,o,h,l,c,v] по mid, число минут, пропущенных из-за дыры > MAX_GAP_S)."""
    pts = sorted((ts_ms, (bids[0][0] + asks[0][0]) / 2) for ts_ms, bids, asks in rows)
    gap_ms = MAX_GAP_S * 1000
    hole_minutes: set[int] = set()
    for (ts0, _), (ts1, _) in zip(pts, pts[1:]):
        if ts1 - ts0 > gap_ms:
            hole_minutes.update(range(ts0 // 60000, ts1 // 60000 + 1))

    buckets: dict[int, list[float]] = {}
    for ts_ms, mid in pts:
        buckets.setdefault(ts_ms // 60000, []).append(mid)

    bars, skipped_gap = [], 0
    for minute in sorted(buckets):
        if minute in hole_minutes:
            skipped_gap += 1
            continue
        mids = buckets[minute]
        bars.append([minute * 60, mids[0], max(mids), min(mids), mids[-1], len(mids)])
    return bars, skipped_gap


def run(arg: dict) -> dict:
    """Задача агента: arg = {"symbol_key", "book_key" (обязателен), "since",
    "until", "lookbacks", "horizons", "draws", "seed"} — оси прокидываются в
    a2.analyze без изменений."""
    book_key = arg.get("book_key")
    if not book_key:
        return {"id": "A2b", "error": "book_key обязателен"}
    symbol_key = arg.get("symbol_key") or book_key
    since, until = arg.get("since"), arg.get("until")

    book_rows, dropped = c1._load_full_book(book_key, since, until)
    if not book_rows:
        return {"id": "A2b", "symbol": symbol_key, "window": [since, until],
                "error": "нет снимков стакана в окне"}

    bars, skipped_gap = _minute_bars_mid(book_rows)
    if not bars:
        return {"id": "A2b", "symbol": symbol_key, "window": [since, until],
                "error": "не построено ни одной минуты (все дыры или снимков нет)"}

    res = a2.analyze(bars,
                      tuple(arg.get("lookbacks", (20, 60, 120, 240))),
                      tuple(arg.get("horizons", (5, 15, 30, 60))),
                      int(arg.get("draws", 200)), int(arg.get("seed", 0)))
    notes = [
        f"снимков стакана отброшено при загрузке (нет обеих сторон/цена<=0): {dropped}",
        f"построено минут mid: {len(bars)}, пропущено из-за дыры > {MAX_GAP_S} с: {skipped_gap}",
        "цена = mid стакана, отскока close нет; сравнивать с A2 на барах того же окна",
        *res["notes"],
    ]
    result = common.report("A2b", symbol_key, [since, until], res["rows"], notes,
                            n_days=res["n_days"], halves=res["halves"],
                            cost_pts=common.round_trip_cost_pts(symbol_key, bars[-1][4]),
                            atr_min_pts=common.atr_minute(bars))
    result["bars_stats"] = {
        "median_abs_close_open": statistics.median(abs(b[4] - b[1]) for b in bars),
        "atr_min_pts": result["atr_min_pts"],
    }
    return result
