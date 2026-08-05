"""Разрешение сторон (allow_long / allow_short) — ось перебора «нужна ли вторая сторона».

Два требования, оба критичны:

1. ДЕФОЛТ 1/1 — БЕЗУСЛОВНЫЙ NO-OP. Параметры вводились посреди пересчёта 21 843 строк
   лидерборда; любое изменение поведения по умолчанию развело бы строки, проверенные
   до и после деплоя.
2. ЗАПРЕЩЁННАЯ СТОРОНА = ВЫХОД В ФЛЭТ, а не «сигнала нет». Игнорируй робот сигнал —
   лонг-онли остался бы сидеть в лонге против развернувшегося рынка вообще без выхода.
"""
import pytest

from trader.lab.strategies.library import REGISTRY, make_on_bar


def _gate(want, allow_long=1, allow_short=1):
    """Та же формула, что в make_on_bar (см. ветку после инверсии сигнала)."""
    p = {"allow_long": allow_long, "allow_short": allow_short}
    if want and ((want > 0 and not int(p.get("allow_long", 1) or 0))
                 or (want < 0 and not int(p.get("allow_short", 1) or 0))):
        return 0
    return want


@pytest.mark.parametrize("want", [1, -1, 0, None])
def test_default_is_no_op(want):
    assert _gate(want) is want


def test_missing_params_are_no_op():
    """Роботы, заведённые ДО появления полей, не должны замолчать."""
    for want in (1, -1, 0, None):
        p: dict = {}
        got = 0 if (want and ((want > 0 and not int(p.get("allow_long", 1) or 0))
                              or (want < 0 and not int(p.get("allow_short", 1) or 0)))) else want
        assert got is want


def test_forbidden_side_flattens_not_ignores():
    assert _gate(-1, allow_short=0) == 0      # шорт запрещён -> ВЫХОД, не -1 и не None
    assert _gate(1, allow_long=0) == 0
    assert _gate(1, allow_short=0) == 1       # разрешённая сторона не тронута
    assert _gate(-1, allow_long=0) == -1


def test_both_off_means_always_flat():
    for want in (1, -1):
        assert _gate(want, allow_long=0, allow_short=0) == 0


def test_every_registry_strategy_exposes_the_axis():
    """Оператор просил добавить во ВСЕ стратегии — значит поле должно быть в схеме
    каждой, иначе на стенде его не выставить и в перебор не включить."""
    missing = [sid for sid, spec in REGISTRY.items()
               if not {"allow_long", "allow_short"} <= {p["key"] for p in spec["params_schema"]}]
    assert not missing, f"нет оси сторон у: {', '.join(sorted(missing))}"


def test_defaults_are_one():
    for sid, spec in REGISTRY.items():
        d = spec["default_params"]
        assert d.get("allow_long") == 1 and d.get("allow_short") == 1, sid


def test_make_on_bar_still_builds():
    assert callable(make_on_bar("macd_shectory1"))
