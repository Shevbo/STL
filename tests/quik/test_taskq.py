"""Очередь задач: проверяем ЕДИНСТВЕННОЕ место, где решается «тратить или нет».

17.08.2026 автономный работник без бюджета и рубильника сжёг пятичасовое окно
тарифа за ночь. Поэтому здесь тестируется не CRUD, а три предохранителя:
рубильник, дневной потолок и арифметика остатка.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from taskq import TIERS, ZONES, budget_left, may_claim  # noqa: E402


def test_switch_stops_the_queue_whatever_the_budget():
    """Рубильник сильнее бюджета: он для человека, который увидел неладное."""
    for flag in ("1", "true", "yes", " 1 "):
        assert "рубильник" in may_claim(flag, spent_day=0, cap_day=1_000_000)


def test_day_cap_stops_handing_out_tasks():
    assert may_claim("0", spent_day=999_999, cap_day=1_000_000) == ""
    assert "потолок" in may_claim("0", spent_day=1_000_000, cap_day=1_000_000)
    assert "потолок" in may_claim("0", spent_day=1_500_000, cap_day=1_000_000)


def test_no_cap_means_no_limit_but_switch_still_works():
    """Потолок 0 = не задан. Это удобно на старте, но рубильник обязан работать
    и без потолка — иначе останавливать петлю будет нечем."""
    assert may_claim("0", spent_day=10**9, cap_day=0) == ""
    assert "рубильник" in may_claim("1", spent_day=0, cap_day=0)


def test_budget_left_never_negative():
    assert budget_left(300, 1000) == 700
    assert budget_left(1200, 1000) == 0


def test_zones_and_tiers_are_closed_lists():
    """Опечатка в зоне означала бы задачу, которую никто никогда не заберёт, а
    опечатка в уровне — торговую задачу, молча ушедшую дешёвой модели."""
    assert "any" in ZONES and "trading" in TIERS
    assert "mechanical" in TIERS
