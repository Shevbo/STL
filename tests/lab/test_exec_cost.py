"""EXEC1 (поверхность издержек взятия ликвидности) на синтетике — реальных
данных здесь нет, они считаются только на i9 (STRICT)."""
from datetime import datetime, timezone

from trader.lab import exec_cost as ec

DAY0 = int(datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())  # вторник, МСК-стенка
MID = 100000.0
TICK = 1.0
LOT = 3


def _side_levels(mid: float, half_spread: float, sign: int, l1_qty: int) -> list[tuple]:
    """5 уровней (цена, объём): L1 = l1_qty, L2..L5 = LOT, шаг TICK вглубь книги."""
    p0 = mid + sign * half_spread
    return [(p0, l1_qty)] + [(p0 + sign * i * TICK, LOT) for i in range(1, 5)]


def _rows(*, n_days=2, hours=3, start_min=11 * 60, wide_minutes=(), thin_head_boundary=False,
          half_spread=5.0):
    """Снимки раз в секунду, n_days x hours часов с start_min. wide_minutes:
    минуты, где полспред расширен до 15 (спред 30). thin_head_boundary: в
    секундах 0-4 граничных минут (minute % 30 == 0) L1 урезан до 1 лота."""
    wide = set(wide_minutes)
    rows = []
    for day in range(n_days):
        day_ts = DAY0 + day * 86400
        for m in range(start_min, start_min + hours * 60):
            hs = 15.0 if m in wide else half_spread
            for sec in range(60):
                l1 = 1 if (thin_head_boundary and m % 30 == 0 and sec < 5) else LOT
                ts = day_ts + m * 60 + sec
                bids = _side_levels(MID, hs, -1, l1)
                asks = _side_levels(MID, hs, 1, l1)
                rows.append((ts * 1000, bids, asks))
    return rows


def _find(rows: list[dict], **kw) -> dict:
    return next(r for r in rows if all(r.get(k) == v for k, v in kw.items()))


def test_cost_scales_with_size_and_marks_deep():
    """cost(1) = полспреда обеими сторонами; cost(10) дороже cost(1) и не deep
    (15 лотов на 5 уровнях); cost(20) всегда deep (не хватает глубины)."""
    res = ec.analyze(_rows(), sizes=(1, 10, 20))
    hour_rows = [r for r in res["rows"] if r["cut"] == "hour"]
    hour = hour_rows[0]["key"]

    for side in ("buy", "sell"):
        c1 = _find(hour_rows, key=hour, side=side, size=1)
        c10 = _find(hour_rows, key=hour, side=side, size=10)
        c20 = _find(hour_rows, key=hour, side=side, size=20)
        assert abs(c1["cost_med"] - 5.0) < 1e-9
        assert c10["cost_med"] > c1["cost_med"]
        assert c10["deep_share"] == 0.0
        assert c20["deep_share"] == 1.0


def test_wide_spread_isolated_to_its_minute_class():
    """Расширение спреда до 30 только в минутах h:00 -> класс h:00 даёт
    spread_med 30, остальные классы (h:30, остальные) — 10."""
    wide = {m for m in range(11 * 60, 14 * 60, 60)}  # 11:00, 12:00, 13:00 (h:00), вне событийных минут
    res = ec.analyze(_rows(wide_minutes=wide))
    class_rows = [r for r in res["rows"] if r["cut"] == "class" and r["side"] is None]

    h00 = _find(class_rows, key="h:00/будни")
    h30 = _find(class_rows, key="h:30/будни")
    rest = _find(class_rows, key="остальные/будни")
    assert h00["spread_med"] == 30.0
    assert h30["spread_med"] == 10.0
    assert rest["spread_med"] == 10.0


def test_second_cut_shows_thin_head_of_boundary_minute():
    """L1 урезан до 1 лота в секундах 0-4 граничных минут -> cost(10) там
    выше, чем в секундах 30-59 тех же минут (глубина нормальная)."""
    res = ec.analyze(_rows(thin_head_boundary=True), sizes=(10,))
    second_rows = [r for r in res["rows"] if r["cut"] == "second"]

    head = _find(second_rows, key="boundary:00", side="buy", size=10)
    tail = _find(second_rows, key="boundary:30", side="buy", size=10)
    assert head["cost_med"] > tail["cost_med"]


def test_out_of_session_snapshots_excluded():
    rows = _rows(start_min=6 * 60, hours=1)  # 06:00-07:00, до сессии (SESSION_START_MIN=07:00)
    res = ec.analyze(rows)
    assert res["rows"] == []
    assert "вне сессии отброшено: 7200 снимков" in res["notes"]


def test_thinning_keeps_notes_and_step():
    res = ec.analyze(_rows(n_days=1, hours=1), max_rows=100)
    assert any("прореживание" in n for n in res["notes"])
    hour_rows = [r for r in res["rows"] if r["cut"] == "hour" and r["side"] is None]
    assert hour_rows[0]["n"] <= 100
