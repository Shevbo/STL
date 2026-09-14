"""Цена пункта прогона: число для движка и честная запись в лидерборд.

13.09.2026 истёкшие контракты (RIZ5, RIH6…) шли в бэктест с point_value=1.0
молча, и лидерборд смешал рубли RIU6 с пунктами RIZ5 одной сетки. Проверяем
границу: известная цена пишется как есть, неизвестная — None, а не 1.0,
которая неотличима от настоящего рубля за пункт у Si.
"""
from trader.api.app import _point_value_of


def test_known_point_value_goes_to_engine_and_record():
    assert _point_value_of({"point_value": 1.687}, "RIU6") == (1.687, 1.687)


def test_real_one_ruble_per_point_is_kept():
    # Si: пункт стоит ровно рубль — это правда, а не заглушка.
    assert _point_value_of({"point_value": 1.0}, "SiU6") == (1.0, 1.0)


def test_unknown_point_value_is_not_recorded_as_one():
    # Истёкший контракт: ISS шаг цены не отдаёт, кэша нет.
    assert _point_value_of(None, "RIZ5") == (1.0, None)
    assert _point_value_of({"point_value": None}, "RI") == (1.0, None)   # склейка
    assert _point_value_of({"point_value": 0}, "RIZ5") == (1.0, None)
    assert _point_value_of({"point_value": "мусор"}, "RIZ5") == (1.0, None)
