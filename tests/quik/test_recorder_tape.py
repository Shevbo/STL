"""Лента всех сделок в архиве рынка: построчно и в формате импортёра.

До 29.09.2026 ленты в архиве не было и быть не могло: агент отдавал её только
раннеру на своей машине, в STL она не приходила вовсе. Единственный день ленты,
25.09, попал в архив импортом из выгрузки терминала. Обнаружилось, когда
понадобилось разобрать эпизод 29.09 14:29-14:52 по RIZ6 — около 10 000
контрактов за 15 минут: тик несёт только last, без стороны и размера сделки.
"""
import json

from trader.quik.recorder import MarketRecorder


def _rec(tmp_path):
    r = MarketRecorder(str(tmp_path))
    assert r.enabled
    return r


def test_tape_batch_becomes_one_line_per_trade(tmp_path):
    r = _rec(tmp_path)
    r._write("trade", {"code": "RIZ6", "received_at_unix_ms": 1790700000000,
                       "trades": [{"price": 84000.0, "qty": 3, "side": 1,
                                   "ts_unix_ms": 1790700000100},
                                  {"price": 84010.0, "qty": 7, "side": 2,
                                   "ts_unix_ms": 1790700000200}]})
    r._close_files()
    path = next(tmp_path.glob("trade-*.jsonl"))
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and r.stats["written"] == 2
    # формат — тот же, что у импортёра выгрузки терминала: иначе один и тот же
    # день архива пришлось бы читать двумя разными способами
    assert set(rows[0]) == {"code", "price", "qty", "side", "received_at_unix_ms",
                            "ts_ms", "num", "source"}
    assert rows[0]["code"] == "RIZ6" and rows[0]["qty"] == 3 and rows[0]["side"] == 1
    assert rows[1]["price"] == 84010.0 and rows[1]["ts_ms"] == 1790700000200
    assert rows[0]["source"] == "agent_stream", "поток и импорт должны быть различимы"


def test_broken_rows_are_skipped_not_fatal(tmp_path):
    r = _rec(tmp_path)
    r._write("trade", {"code": "RIZ6", "received_at_unix_ms": 1,
                       "trades": [{"price": 0, "qty": 5},            # нет цены
                                  {"price": 84000.0, "qty": 0},      # нет объёма
                                  {"price": "нечисло", "qty": 1},    # мусор
                                  {"price": 84000.0, "qty": 2}]})    # годная
    r._close_files()
    rows = [json.loads(x) for x in
            next(tmp_path.glob("trade-*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["qty"] == 2
    assert r.stats["written"] == 1


def test_empty_batch_writes_nothing(tmp_path):
    r = _rec(tmp_path)
    r._write("trade", {"code": "RIZ6", "trades": []})
    r._close_files()
    assert r.stats["written"] == 0


def test_tape_frames_are_recognised():
    assert MarketRecorder.KINDS.get("tape") == "trade"
