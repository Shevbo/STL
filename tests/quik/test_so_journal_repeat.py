"""Повтор одного и того же события пишется раз в минуту, а не каждую секунду.

01.10.2026 задержанный карантином ВХОД написал 120 одинаковых строк `held` за три
минуты. Единственное, что в них было важного — ПРИЧИНА в поле detail — оказалось
погребено под сотней своих же копий: чтобы её найти, пришлось грепать журнал.
Журнал, в котором тонет причина, работает против того, ради чего он ведётся.
"""
import json

from trader.quik import so_journal


class _SO:
    so_id = "6d3a962d46"
    kind = "tp"
    code = "RIZ6"
    side = "sell"
    qty = 5
    status = "armed"
    parent_id = ""


def _rows(d):
    out = []
    for p in d.iterdir():
        for line in p.read_text(encoding="utf-8").splitlines():
            out.append(json.loads(line))
    return out


def _reset():
    so_journal._last_seen.clear()          # noqa: SLF001 — состояние между тестами


def test_identical_event_every_second_is_written_once_a_minute(tmp_path):
    _reset()
    d = str(tmp_path)
    now = 1_790_838_460_000
    for i in range(180):                   # три минуты по разу в секунду
        so_journal.record("held", _SO(), "сторож STL",
                          "карантин после слепоты: вход отложен",
                          now_ms=now + i * 1000, directory=d)
    rows = _rows(tmp_path)
    assert len(rows) == 3, f"ожидали по строке в минуту, получили {len(rows)}"
    assert all(r["detail"].startswith("карантин") for r in rows)


def test_changed_reason_is_written_immediately(tmp_path):
    """Сменилась причина — строка идёт сразу, ждать минуту нельзя."""
    _reset()
    d = str(tmp_path)
    now = 1_790_838_460_000
    so_journal.record("held", _SO(), "сторож STL", "карантин", now_ms=now, directory=d)
    so_journal.record("held", _SO(), "сторож STL", "карантин", now_ms=now + 1000, directory=d)
    so_journal.record("held", _SO(), "сторож STL",
                      "в терминале жива своя стоп-заявка", now_ms=now + 2000, directory=d)
    rows = _rows(tmp_path)
    assert len(rows) == 2
    assert rows[-1]["detail"] == "в терминале жива своя стоп-заявка"


def test_different_orders_do_not_suppress_each_other(tmp_path):
    _reset()
    d = str(tmp_path)
    now = 1_790_838_460_000

    class _Other(_SO):
        so_id = "0fc8ab5881"

    so_journal.record("held", _SO(), "сторож", "одна причина", now_ms=now, directory=d)
    so_journal.record("held", _Other(), "сторож", "одна причина", now_ms=now + 1, directory=d)
    assert len(_rows(tmp_path)) == 2, "разные заявки глушить друг друга не вправе"


def test_rare_events_are_never_suppressed(tmp_path):
    """Срабатывание, отмена, осиротение — события редкие и обязаны проходить все."""
    _reset()
    d = str(tmp_path)
    now = 1_790_838_460_000
    for i, ev in enumerate(("created", "fired", "cancelled", "orphaned")):
        so_journal.record(ev, _SO(), "сторож", f"событие {ev}",
                          now_ms=now + i * 100, directory=d)
    assert len(_rows(tmp_path)) == 4
