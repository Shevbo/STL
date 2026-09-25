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
