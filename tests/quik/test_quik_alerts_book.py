"""Книга тревог агента для экранов STL (исполнительный модуль, разделы 12 и 15).

Держим границы, на которых экран может соврать: подтверждённая тревога не мигает,
WARN не мигает никогда, тревога вне флэш-списка не мигает, а запись в книгу не
ломает пересылку в Telegram.
"""

from trader.api.quik_alerts import FLASH_CODES, AlertBook, RecordingForwarder
from trader.quik.alerts import SEVERITY_CRITICAL, SEVERITY_WARN


def _alert(code="EXIT_NOT_FILLED", severity=SEVERITY_CRITICAL, raised=1_789_000_000_000,
           message="выход не налился за 10 с"):
    return {"severity": severity, "code": code, "message": message,
            "raised_at_unix_ms": raised}


class _FakeSender:
    def __init__(self):
        self.sent: list[str] = []

    async def __call__(self, text: str) -> None:
        self.sent.append(text)


def test_flash_shows_unacked_critical_from_flash_list():
    book = AlertBook()
    book.add(_alert(), "WIN-QUIK01")
    assert book.flash() == [{"code": "EXIT_NOT_FILLED", "text": "выход не налился за 10 с",
                             "ts_ms": 1_789_000_000_000}]


def test_ack_stops_flashing_but_keeps_history():
    book = AlertBook()
    book.add(_alert(), "WIN-QUIK01")
    assert book.ack("EXIT_NOT_FILLED", 1_789_000_000_000, now_ms=5) == 1
    assert book.flash() == []
    assert book.items()[0]["acked_ms"] == 5          # в истории осталась, с отметкой
    assert book.ack("EXIT_NOT_FILLED", 1_789_000_000_000, now_ms=6) == 0   # второй раз нечего


def test_warn_never_flashes_even_from_flash_list():
    book = AlertBook()
    book.add(_alert(code="GO_EXCEEDED", severity=SEVERITY_WARN), "a")
    assert book.flash() == []
    assert book.items()[0]["severity_label"] == "WARN"


def test_code_outside_flash_list_does_not_flash():
    book = AlertBook()
    book.add(_alert(code="STOP_EXPIRED"), "a")
    assert "STOP_EXPIRED" not in FLASH_CODES
    assert book.flash() == []


def test_items_newest_first_and_bounded():
    book = AlertBook(keep=3)
    for i in range(5):
        book.add(_alert(raised=i), "a")
    assert [x["raised_at"] for x in book.items()] == [4, 3, 2]


async def test_recording_forwarder_records_and_still_sends():
    book = AlertBook()
    sender = _FakeSender()
    fwd = RecordingForwarder(book, tg_token="tok", tg_chat_id="chat", cooldown_sec=60,
                             send=sender)
    await fwd.forward(_alert(), agent_host="WIN-QUIK01")
    assert book.items()[0]["code"] == "EXIT_NOT_FILLED"
    assert len(sender.sent) == 1                        # Telegram по-прежнему получил


async def test_recording_forwarder_records_even_without_telegram():
    book = AlertBook()
    fwd = RecordingForwarder(book, tg_token="", tg_chat_id="", cooldown_sec=60)
    await fwd.forward(_alert(), agent_host="WIN-QUIK01")
    assert len(book.items()) == 1                       # экран видит, даже если TG не настроен
