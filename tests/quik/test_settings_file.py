"""Один файл настроек живой торговли, правка без рестарта.

Распоряжение оператора 02.10.2026: «сделай все настройки в одном файле в известном
месте, который также правится в настройках вотчера в веб STL». До этого предел жил
в переменной окружения, читался раз при старте и менялся только рестартом боевого
процесса — в тот же день это стоило простоя: сетка уткнулась в max_working_contracts,
и поднимать его пришлось посреди торгов.
"""
import json

from trader.quik import settings_file
from trader.quik.limits import OrderLimits


class S:
    quik_trading_enabled = True
    quik_instrument_whitelist = "RIZ6,GZZ6"
    quik_max_contracts_per_order = 75
    quik_max_working_contracts = 70
    quik_price_collar_frac = 0.002
    quik_daily_order_cap = 500


def test_without_a_file_values_come_from_env_and_nothing_is_written(tmp_path):
    """ЧТЕНИЕ ПРЕДЕЛОВ НЕ ПИШЕТ НА ДИСК. Оно происходит на КАЖДУЮ заявку, и
    побочных действий у него быть не должно: первая же версия создавала файл при
    чтении и тут же сломала четыре чужих теста — они задавали пределы своей
    настройкой, а получали файл от соседнего прогона."""
    p = str(tmp_path / "limits.json")
    v = settings_file.load(S(), p)
    assert v["max_working_contracts"] == 70
    assert v["instrument_whitelist"] == ["RIZ6", "GZZ6"]
    assert not (tmp_path / "limits.json").exists(), "чтение создало файл"


def test_the_file_wins_over_env_and_reloads_by_mtime(tmp_path):
    """Правка действует СРАЗУ: файл перечитывается по mtime, рестарт не нужен."""
    p = str(tmp_path / "limits.json")
    settings_file.save({**settings_file._from_env(S()), "max_working_contracts": 250}, p)
    assert settings_file.load(S(), p)["max_working_contracts"] == 250
    # правим файл «снаружи», как это сделает экран или рука
    raw = json.loads(open(p, encoding="utf-8").read())
    raw["max_working_contracts"] = 300
    import os
    import time
    time.sleep(0.01)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    os.utime(p, (time.time() + 1, time.time() + 1))
    assert settings_file.load(S(), p)["max_working_contracts"] == 300


def test_a_broken_file_falls_back_to_env_instead_of_empty_limits(tmp_path):
    """БИТЫЙ ФАЙЛ НЕ ПРИМЕНЯЕМ. Пустые пределы это «разрешить всё» — опаснее
    любого отказа."""
    p = str(tmp_path / "limits.json")
    open(p, "w", encoding="utf-8").write("{это не json")
    v = settings_file.load(S(), p)
    assert v["max_working_contracts"] == 70, "вернулись к окружению"
    assert v["instrument_whitelist"] == ["RIZ6", "GZZ6"]


def test_unknown_keys_are_ignored_and_missing_ones_keep_env(tmp_path):
    """Чужой ключ не становится пределом, забытый не исчезает молча."""
    p = str(tmp_path / "limits.json")
    open(p, "w", encoding="utf-8").write(json.dumps(
        {"max_working_contracts": 123, "посторонний": 1}))
    v = settings_file.load(S(), p)
    assert v["max_working_contracts"] == 123
    assert v["daily_order_cap"] == 500, "не присланное берётся из окружения"
    assert "посторонний" not in v


def test_bad_types_do_not_break_the_whole_file(tmp_path):
    p = str(tmp_path / "limits.json")
    open(p, "w", encoding="utf-8").write(json.dumps(
        {"max_working_contracts": "много", "daily_order_cap": 900}))
    v = settings_file.load(S(), p)
    assert v["max_working_contracts"] == 70, "негодное значение — берём из окружения"
    assert v["daily_order_cap"] == 900, "годное рядом применяется"


def test_ensure_creates_the_file_once(tmp_path):
    p = str(tmp_path / "limits.json")
    settings_file.ensure(S(), p)
    assert (tmp_path / "limits.json").exists()
    raw = json.loads(open(p, encoding="utf-8").read())
    assert raw["max_working_contracts"] == 70


def test_order_limits_read_the_file(tmp_path, monkeypatch):
    """Пределы, по которым гейтятся заявки, берутся из файла, а не из окружения."""
    p = str(tmp_path / "limits.json")
    settings_file.save({**settings_file._from_env(S()),
                        "max_working_contracts": 250,
                        "instrument_whitelist": ["RIZ6"]}, p)
    monkeypatch.setattr(settings_file, "PATH", p)
    lim = OrderLimits.from_settings(S())
    assert lim.max_working_contracts == 250
    assert lim.instrument_whitelist == ("RIZ6",)
