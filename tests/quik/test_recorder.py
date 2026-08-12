"""Архив сырого рынка: пишет что надо, и НИКОГДА не мешает торговле.

Второе важнее первого: сбор данных не имеет права уронить приём кадров агента.
"""
import json

import pytest

from trader.quik.recorder import MarketRecorder, _to_dict


def _arm(rec: MarketRecorder) -> None:
    """Поднять очередь БЕЗ фонового писателя: иначе он конкурирует с _drain."""
    import asyncio
    import os
    os.makedirs(rec.root, exist_ok=True)
    rec._q = asyncio.Queue(maxsize=20_000)


def _drain(rec: MarketRecorder) -> None:
    """Синхронно выгрести очередь ТЕМ ЖЕ путём, что и писательский таск: с
    конвертацией и гейтом. Иначе тест проверял бы не тот код, что работает."""
    while rec._q is not None and not rec._q.empty():
        kind, item = rec._q.get_nowait()
        payload = item if isinstance(item, dict) else _to_dict(item)
        if payload is None:
            rec.stats["errors"] += 1
            continue
        if rec._same_as_last(kind, payload):
            rec.stats["skipped_same"] += 1
            continue
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
    _arm(rec)
    book = {"code": "BRU6", "bids": [{"price": 85.0, "quantity": 3}],
            "asks": [{"price": 85.01, "quantity": 5}], "received_at_unix_ms": 1}
    rec.record("book", book)
    rec.record("book", {**book, "received_at_unix_ms": 2})        # только время
    rec.record("book", {**book, "received_at_unix_ms": 3})
    rec.record("book", {**book, "asks": [{"price": 85.02, "quantity": 5}],
                        "received_at_unix_ms": 4})                # цена изменилась
    _drain(rec)
    assert rec.stats["skipped_same"] == 2
    rows = [json.loads(x) for x in
            open(next(tmp_path.glob("book-*.jsonl")), encoding="utf-8")]
    assert len(rows) == 2
    assert rows[0]["received_at_unix_ms"] == 1 and rows[1]["received_at_unix_ms"] == 4
    rec._close_files()


@pytest.mark.asyncio
async def test_full_queue_drops_frames_instead_of_blocking(tmp_path):
    """Полная очередь ОБЯЗАНА терять кадры, а не создавать давление на приём:
    архив не стоит ни одной задержки в торговом пути."""
    rec = MarketRecorder(root=str(tmp_path))
    _arm(rec)
    import asyncio
    rec._q = asyncio.Queue(maxsize=2)
    for i in range(10):
        rec.record("tick", {"code": "RIU6", "last": float(i)})
    assert rec._q.qsize() == 2
    assert rec.stats["dropped"] == 8
    rec._close_files()


@pytest.mark.asyncio
async def test_record_never_raises_on_broken_payload(tmp_path):
    """Кадр непригодной формы гасится внутри архива. Исключение отсюда убило бы
    сессию агента — тот же класс, что UnicodeEncodeError в пути филла (13.07)."""
    rec = MarketRecorder(root=str(tmp_path))
    _arm(rec)

    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    rec.record("book", Boom())            # постановка не должна бросить
    rec.record_frame("order_book", None)  # msg=None -> getattr упадёт внутри
    _drain(rec)                           # и дренаж тоже не должен бросить
    assert rec.stats["errors"] >= 1
    rec._close_files()


def test_only_execution_relevant_frames_are_archived():
    """Heartbeat, диагностика и статусы роботов для моделирования исполнения
    бесполезны — они только раздули бы файлы."""
    kinds = MarketRecorder.KINDS
    assert set(kinds) == {"order_book", "tick", "order_update",
                          "trans_reply", "execution_update"}
    assert "heartbeat" not in kinds and "robot_status_report" not in kinds


@pytest.mark.asyncio
async def test_live_file_is_plain_text_and_readable_while_open(tmp_path):
    """Первая версия писала gzip в режиме ДОЗАПИСИ, и файлы оказались нечитаемы:
    поток не завершён пока файл открыт, zcat падал на середине. Живой архив
    обязан читаться в любой момент и переживать падение процесса."""
    rec = MarketRecorder(root=str(tmp_path))
    _arm(rec)
    rec.record("tick", {"code": "RIU6", "last": 1.0})
    rec.record("tick", {"code": "RIU6", "last": 2.0})
    while not rec._q.empty():
        kind, item = rec._q.get_nowait()
        if not rec._same_as_last(kind, item):
            rec._write(kind, item)
    rec._files["tick"].flush()                      # файл ещё ОТКРЫТ
    path = next(tmp_path.glob("tick-*.jsonl"))
    rows = [json.loads(x) for x in open(path, encoding="utf-8")]
    assert [r["last"] for r in rows] == [1.0, 2.0]
    rec._close_files()


@pytest.mark.asyncio
async def test_rotation_compresses_a_closed_file(tmp_path):
    """Сжимаем ОДИН раз при смене суток — там файл уже закрыт и целостен."""
    import gzip as _gz
    rec = MarketRecorder(root=str(tmp_path))
    _arm(rec)
    rec._day = "2026-08-11"
    rec._write("tick", {"code": "RIU6", "last": 1.0})
    rec._rotate()
    gz = next(tmp_path.glob("tick-*.jsonl.gz"))
    assert json.loads(_gz.open(gz, "rt", encoding="utf-8").read())["last"] == 1.0
    assert list(tmp_path.glob("tick-*.jsonl")) == []   # исходник убран
