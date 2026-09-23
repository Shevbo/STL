"""Строка лидерборда -> вектор одиночного прогона: ничего не выдумывает молча.

Просьба ui-ux 23.09.2026: без этой функции вектор собирается руками из params и
однажды соберётся неверно — тогда экран покажет одно, а посчитается другое.
"""
import pytest

from trader.lab.row_to_run import row_to_run


def _row(**over):
    row = {"strategy": "macd_shectory1", "symbol": "RIZ6",
           "date_from": "2026-09-18", "date_to": "2026-09-23",
           "params": {"symbol": "RIZ6", "book_key": "bookRIZ6d0921",
                      "tp_atr": 80.0, "sl_pct": 100, "fast": 57, "slow": 48}}
    row.update(over)
    return row


def test_service_keys_are_not_params():
    spec = row_to_run(_row())
    assert "book_key" not in spec.params          # в одиночном прогоне не нужен
    assert spec.params["symbol"] == "RIZ6"        # символ остаётся, движок его читает
    assert spec.symbol == "RIZ6"


def test_window_comes_from_the_row():
    """Окно и символ берутся ИЗ СТРОКИ: иначе кривая врёт на миллионы (уже было)."""
    spec = row_to_run(_row())
    assert (spec.date_from, spec.date_to) == ("2026-09-18", "2026-09-23")
    job = spec.to_job()
    assert job["dateFrom"].startswith("2026-09-18")
    assert job["symbol"] == "RIZ6"


def test_float_from_json_becomes_int():
    spec = row_to_run(_row())
    assert spec.params["tp_atr"] == 80 and isinstance(spec.params["tp_atr"], int)


def test_new_axis_is_filled_but_reported():
    """Ось, появившаяся после прогона, берёт дефолт — и это ПРЕДУПРЕЖДЕНИЕ."""
    spec = row_to_run(_row())
    assert "flip_close_loss" in spec.params
    assert any("flip_close_loss" in w for w in spec.warnings), spec.warnings


def test_stale_key_is_dropped_and_reported():
    row = _row()
    row["params"]["ось_которой_нет"] = 7
    spec = row_to_run(row)
    assert "ось_которой_нет" not in spec.params
    assert any("ось_которой_нет" in w for w in spec.warnings)


def test_clean_row_has_no_warnings_about_known_keys():
    spec = row_to_run(_row())
    assert not any("tp_atr" in w or "sl_pct" in w for w in spec.warnings)


def test_missing_symbol_is_an_error_not_a_guess():
    row = _row(symbol=None)
    row["params"].pop("symbol")
    with pytest.raises(ValueError):
        row_to_run(row)
