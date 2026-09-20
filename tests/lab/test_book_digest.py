"""Выжимка стакана для i9: сборка из архива и чтение обратно.

ЗАЧЕМ (20.09.2026). Перебор считает ТОЛЬКО i9, а туда ведёт один канал данных —
`/api/v1/agent/bars/<key>`, отдающий `agent_bars/<key>.json`. Сырой архив (сотня
мегабайт) этим каналом не поедет, поэтому он сжимается в один снимок на минуту.
Тест сторожит две вещи, на которых такой конвейер ломается молча: что время уже
приведено к шкале баров (иначе заявка исполнится по стакану трёхчасовой
давности) и что выжимка читается обратно в ту же структуру, что сырой архив.
"""
import json

from trader.lab.book_replay import MSK_SHIFT, load_book, load_digest

from scripts.book_digest import build


def _write(tmp_path, rows, name="book-2026-09-16.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def _snap(ms: int, bid: float, ask: float, qty: int = 5, code: str = "RIU6"):
    return {"code": code, "received_at_unix_ms": str(ms),
            "bids": [{"price": bid - i, "quantity": str(qty)} for i in range(5)],
            "asks": [{"price": ask + i, "quantity": str(qty)} for i in range(5)]}


def test_last_snapshot_of_the_minute_wins(tmp_path):
    """В минуте несколько снимков — берём последний: он ближе к закрытию бара."""
    base = 1_789_516_800_000                      # ровная минута
    _write(tmp_path, [_snap(base, 99, 101), _snap(base + 30_000, 98, 102)])
    d = build(str(tmp_path), "RIU6", None, None)
    assert len(d["rows"]) == 1
    row = d["rows"][0]
    assert row[1] == 98.0 and row[1 + 2 * 5] == 102.0


def test_time_is_already_in_bar_scale(tmp_path):
    base = 1_789_516_800_000
    _write(tmp_path, [_snap(base, 99, 101)])
    d = build(str(tmp_path), "RIU6", None, None)
    assert d["rows"][0][0] == base // 1000 + MSK_SHIFT


def test_digest_reads_back_like_the_raw_archive(tmp_path):
    base = 1_789_516_800_000
    p = _write(tmp_path, [_snap(base, 99, 101)])
    raw_t, raw_b = load_book([str(p)], "RIU6")
    d = build(str(tmp_path), "RIU6", None, None)
    dig_t, dig_b = load_digest(d["rows"])
    assert dig_t == raw_t
    assert dig_b[0][0][:5] == raw_b[0][0][:5]     # биды: те же 5 уровней
    assert dig_b[0][1][:5] == raw_b[0][1][:5]     # аски


def test_other_codes_and_date_window_are_filtered(tmp_path):
    base = 1_789_516_800_000
    _write(tmp_path, [_snap(base, 99, 101, code="SiZ6")])
    assert build(str(tmp_path), "RIU6", None, None)["rows"] == []
    _write(tmp_path, [_snap(base, 99, 101)], name="book-2026-08-01.jsonl")
    assert build(str(tmp_path), "RIU6", "2026-07-01", None)["rows"] != []   # день в окне
    assert build(str(tmp_path), "RIU6", "2026-09-01", None)["rows"] == []   # левее окна
    assert build(str(tmp_path), "RIU6", None, "2026-07-01")["rows"] == []   # правее окна
