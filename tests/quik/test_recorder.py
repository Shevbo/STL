"""Архив сырого рынка: пишет что надо, и НИКОГДА не мешает торговле.

Второе важнее первого: сбор данных не имеет права уронить приём кадров агента.
"""
import gzip
import json

import pytest

from trader.quik.recorder import MarketRecorder


def _drain(rec: MarketRecorder) -> None:
    """Синхронно выгрести очередь в файлы (в тесте не гоняем фоновый таск)."""
    while rec._q is not None and not rec._q.empty():
        kind, payload = rec._q.get_nowait()
        rec._write(kind, payload)
    rec._close_files()


@pytest.mark.asyncio
async def test_disabled_by_default_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIK_RECORD_DIR", raising=False)
    rec = MarketRecorder()
    assert rec.enabled is False
    rec.record_frame("order_book", object())      # не должно даже пытаться
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_unchanged_book_is_not_written_twice(tmp_path):
    """Lua переставляет метку времени стакана КАЖДУЮ секунду, даже когда сам
    стакан не менялся. Гейт по времени пропустил бы 60 тысяч одинаковых снимков
    в час; гейт по содержимому оставляет только реальные изменения."""
    rec = MarketRecorder(root=str(tmp_path))
    await rec.start()
    book = {"code": "BRU6", "bids": [{"price": 85.0, "quantity": 3}],
            "asks": [{"price": 85.01, "quantity": 5}], "received_at_unix_ms": 1}
    rec.record("book", book)
    rec.record("book", {**book, "received_at_unix_ms": 2})        # только время
    rec.record("book", {**book, "received_at_unix_ms": 3})
    assert rec.stats["skipped_same"] == 2
    rec.record("book", {**book, "asks": [{"price": 85.02, "quantity": 5}],
                        "received_at_unix_ms": 4})                # цена изменилась
    _drain(rec)
    rows = [json.loads(x) for x in
            gzip.open(next(tmp_path.glob("book-*.jsonl.gz")), "rt", encoding="utf-8")]
    assert len(rows) == 2
    assert rows[0]["received_at_unix_ms"] == 1 and rows[1]["received_at_unix_ms"] == 4
    await rec.stop()


@pytest.mark.asyncio
async def test_full_queue_drops_frames_instead_of_blocking(tmp_path):
    """Полная очередь ОБЯЗАНА терять кадры, а не создавать давление на приём:
    архив не стоит ни одной задержки в торговом пути."""
    rec = MarketRecorder(root=str(tmp_path))
    await rec.start()
    import asyncio
    rec._q = asyncio.Queue(maxsize=2)
    for i in range(10):
        rec.record("tick", {"code": "RIU6", "last": float(i)})
    assert rec._q.qsize() == 2
    assert rec.stats["dropped"] == 8
    await rec.stop()


@pytest.mark.asyncio
async def test_record_never_raises_on_broken_payload(tmp_path):
    """Кадр непригодной формы гасится внутри архива. Исключение отсюда убило бы
    сессию агента — тот же класс, что UnicodeEncodeError в пути филла (13.07)."""
    rec = MarketRecorder(root=str(tmp_path))
    await rec.start()

    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    rec.record("book", Boom())          # не должно бросить
    rec.record_frame("order_book", None)  # msg=None -> конвертация упадёт внутри
    assert rec.stats["errors"] >= 1
    await rec.stop()


def test_only_execution_relevant_frames_are_archived():
    """Heartbeat, диагностика и статусы роботов для моделирования исполнения
    бесполезны — они только раздули бы файлы."""
    kinds = MarketRecorder.KINDS
    assert set(kinds) == {"order_book", "tick", "order_update",
                          "trans_reply", "execution_update"}
    assert "heartbeat" not in kinds and "robot_status_report" not in kinds
