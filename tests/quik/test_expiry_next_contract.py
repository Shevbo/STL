"""Куда переезжать с контракта (docs/design/expiry-roll.md).

Главный случай - ДЕНЬ ЭКСПИРАЦИИ: старый front_contract включает сегодняшний последний
торговый день и вернул бы тот же самый контракт, то есть кампания переложила бы робота
саму на себя. Ответ обязан быть следующим контрактом серии.
"""
from datetime import date

from trader.lab.contract_roll import front_contract  # noqa: F401 — соседний путь, не трогаем
from trader.quik.expiry import expiry_of, next_contract

ROWS = [
    ("RIU6", "2026-09-17"), ("RIZ6", "2026-12-17"), ("RIH7", "2027-03-18"),
    ("SiU6", "2026-09-17"), ("SiZ6", "2026-12-17"),
    ("BRV6", "2026-10-01"), ("BRX6", "2026-11-02"), ("BRZ6", "2026-12-01"),
    ("GZU6", "2026-09-17"), ("GZZ6", "2026-12-17"),
    ("MXU6", "2026-09-17"),
]


def test_quarterly_roll_on_expiry_day():
    # 17.09 - последний торговый день RIU6: переезд обязан быть на RIZ6, не на RIU6
    assert next_contract("RIU6", ROWS, today=date(2026, 9, 17)) == "RIZ6"
    assert next_contract("RIU6", ROWS, today=date(2026, 9, 16)) == "RIZ6"
    assert next_contract("SiU6", ROWS, today=date(2026, 9, 16)) == "SiZ6"


def test_monthly_series_goes_one_step_not_to_december():
    # BR месячный: с октябрьского едем на ноябрьский, а не сразу на декабрьский
    assert next_contract("BRV6", ROWS, today=date(2026, 9, 16)) == "BRX6"
    assert next_contract("BRX6", ROWS, today=date(2026, 10, 2)) == "BRZ6"


def test_expired_contract_not_in_iss_falls_back_to_today():
    # BRU6 истёк 01.09 и снят с торгов: в выдаче ISS его уже нет, точка отсчёта - сегодня
    assert next_contract("BRU6", ROWS, today=date(2026, 9, 16)) == "BRV6"
    assert next_contract("BRU6", ROWS, today=date(2026, 10, 1)) == "BRV6"
    assert next_contract("BRU6", ROWS, today=date(2026, 10, 2)) == "BRX6"


def test_last_contract_of_series_has_no_next():
    assert next_contract("RIH7", ROWS, today=date(2026, 9, 16)) is None
    assert next_contract("MXU6", ROWS, today=date(2026, 9, 16)) is None


def test_not_a_contract_and_expiry_lookup():
    assert next_contract("RTSI", ROWS) is None
    assert next_contract("", ROWS) is None
    assert expiry_of("RIU6", ROWS) == date(2026, 9, 17)
    assert expiry_of("НЕТ", ROWS) is None
