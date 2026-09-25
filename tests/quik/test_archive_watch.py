"""Сторож архива: тревога, когда биржа торгует, а данные не пишутся."""

from trader.quik.archive_watch import SILENT_SEC, verdict

NOW = 1_790_000_000_000


def test_growing_archive_is_quiet():
    bad, age, since = verdict(written=100, last_written=90, last_change_ms=NOW - 10 * 60_000,
                              market_open=True, now_ms=NOW)
    assert bad is False and age == 0 and since == NOW


def test_silence_while_the_market_trades_raises():
    """25.09.2026: сбор умер в 16:29, заметили в одиннадцать вечера."""
    bad, age, _ = verdict(written=100, last_written=100,
                          last_change_ms=NOW - (SILENT_SEC + 60) * 1000,
                          market_open=True, now_ms=NOW)
    assert bad is True and age >= SILENT_SEC


def test_silence_while_the_market_is_closed_is_normal():
    bad, age, _ = verdict(written=100, last_written=100,
                          last_change_ms=NOW - 3 * 3600_000,
                          market_open=False, now_ms=NOW)
    assert bad is False and age == 0


def test_unknown_session_is_treated_defensively():
    """«Не знаю» у оракула — не повод считать архив исправным."""
    bad, _, _ = verdict(written=100, last_written=100,
                        last_change_ms=NOW - (SILENT_SEC + 1) * 1000,
                        market_open=None, now_ms=NOW)
    assert bad is True


def test_short_silence_does_not_raise():
    bad, age, _ = verdict(written=100, last_written=100,
                          last_change_ms=NOW - 60_000, market_open=True, now_ms=NOW)
    assert bad is False and age == 60


# ---- разбор выгрузки таблицы сделок QUIK ----
# Формат коварен: разделитель полей запятая, и ДЕСЯТИЧНЫЙ разделитель цены тоже
# запятая, поэтому колонок в строке больше, чем в заголовке, и разбор слева ломает
# цену пополам. 25.09.2026 из такой выгрузки восстанавливали день, потерянный
# сбором.
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "import_quik_trades",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "import_quik_trades.py"))
_imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_imp)


def test_instrument_title_becomes_a_forts_code():
    assert _imp.code_from_title("RTS-12.26 [ФОРТС фьючерсы]") == "RIZ6"
    assert _imp.code_from_title("Si-12.26 [ФОРТС фьючерсы]") == "SiZ6"
    assert _imp.code_from_title("GOLD-12.26 [x]") == "GDZ6"
    assert _imp.code_from_title("BR-9.26 [x]") == "BRU6"
    assert _imp.code_from_title("что-то незнакомое") == "что-то незнакомое"


def test_price_split_by_the_field_separator_is_glued_back():
    """«96,52» — это ОДНА цена, а не два поля."""
    assert _imp.parse_tail_row("1,6:59:12,BR-12.26 [x],96,52,5,Продажа,") == (
        "6:59:12", "BRZ6", 96.52, 5, "Продажа")


def test_thousands_separator_does_not_break_the_price():
    time_s, code, price, qty, _ = _imp.parse_tail_row(
        "60000,10:51:01,RTS-12.26 [x],85\xa0380,1,Купля,")
    assert (code, price, qty) == ("RIZ6", 85380.0, 1)
    # И то и другое разом: тысячи пробелом, дробная часть запятой.
    assert _imp.parse_tail_row("2,12:43:54,GOLD-12.26 [x],4\xa0362,6,3,Купля,")[2] == 4362.6


def test_broken_row_is_skipped_not_guessed():
    assert _imp.parse_tail_row("мусор") is None
    assert _imp.parse_tail_row("1,6:59:12,BR-12.26 [x],цена,5,Продажа,") is None
