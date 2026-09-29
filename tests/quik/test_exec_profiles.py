"""Профили исполнения умной заявки: вход и выход, три штатных, правка с диска.

Заказ оператора 29.09.2026: секунды фаз не универсальны. На тихом рынке и малой
позиции можно ждать по три минуты и выйти по хорошей цене; на быстром рынке и
крупной позиции ждать нельзя. Поэтому не зашитые числа, а профили, которые он
меняет сам.
"""
import json

from trader.quik import exec_profiles as ep


def test_three_stock_profiles():
    p = ep.load("нет-такого-файла.json")
    assert set(p) == {"aggressive", "active", "normal"}
    assert (p["aggressive"]["hold_sec"], p["aggressive"]["chase_sec"]) == (10, 10)
    assert (p["active"]["hold_sec"], p["active"]["chase_sec"]) == (180, 180)
    assert p["aggressive"]["market"] is True and p["active"]["market"] is True
    # «нормальный» — это НЕ нулевые фазы: ноль означал бы «сразу по рынку»,
    # а здесь по рынку не бьём никогда, заявка стоит лимитом как до 29.09
    assert p["normal"]["market"] is False


def test_disk_overrides_and_survives_garbage(tmp_path):
    path = str(tmp_path / "p.json")
    ep.save({"active": {"hold_sec": 300, "chase_sec": 60, "chase_every_sec": 15,
                        "market": True, "title": "мой активный"}}, path)
    p = ep.load(path)
    assert p["active"]["hold_sec"] == 300 and p["active"]["title"] == "мой активный"
    assert p["aggressive"]["hold_sec"] == 10, "штатные остаются рядом с правками"
    # битый файл не роняет торговлю и не оставляет заявку без профиля
    open(path, "w", encoding="utf-8").write("{это не json")
    assert set(ep.load(path)) == {"aggressive", "active", "normal"}
    # отрицательные секунды подтягиваются к нулю, а не уходят в прошлое
    open(path, "w", encoding="utf-8").write(json.dumps({"active": {"hold_sec": -5}}))
    assert ep.load(path)["active"]["hold_sec"] == 0


def test_unknown_profile_falls_back_not_refuses(tmp_path):
    """Опечатка в имени не должна оставлять заявку без доведения молча."""
    got = ep.resolve("агрессиный", str(tmp_path / "p.json"))
    assert got["name"] == ep.DEFAULT_PROFILE == "aggressive"
    assert ep.resolve("", str(tmp_path / "p.json"))["name"] == "aggressive"
    assert ep.resolve("active", str(tmp_path / "p.json"))["hold_sec"] == 180
