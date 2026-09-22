"""Гейт режима «только вход»: в позиции молчит, из флэта решает.

ЗАЧЕМ ОСЬ. Запрещённая сторона обнуляет want, а want=0 в позиции — это флип,
то есть выход в флэт. Значит гейт режима по умолчанию работает ВЫХОДОМ по
средней: стопом при reg_mode=1 и тейком при reg_mode=2. Замер 22.09.2026 показал
это симметрией: при стопе 3% ОБА режима улучшали базу в 118 клетках из 135, при
стопе 1% ОБА ухудшали в 120 из 135 — знак задавал стоп, а не метка рынка. Саму
метку такой осью измерить нельзя.

Тест зеркалит арифметику гейта из make_on_bar, как соседние тесты движка.
"""
from trader.lab.strategies.library import REGISTRY


def gate(px: float, ref: float, params: dict, in_position: bool) -> tuple[int, int]:
    """Разрешённые стороны после гейта режима: (лонг, шорт)."""
    a_long = a_short = 1
    band = px * float(params.get("reg_band", 0) or 0) / 10000.0
    up, down = px > ref + band, px < ref - band
    if int(params.get("reg_mode", 1) or 1) == 2:
        up, down = down, up
    if int(params.get("reg_entry_only", 0) or 0) and up != down and in_position:
        up = down = False
    if up:
        a_short = 0
    elif down:
        a_long = 0
    return a_long, a_short


TREND = {"reg_n": 240, "reg_band": 20, "reg_mode": 1}


def test_gate_blocks_entry_against_trend_when_flat():
    assert gate(85000, 84000, TREND, in_position=False) == (1, 0)   # рост: шорт нельзя
    assert gate(83000, 84000, TREND, in_position=False) == (0, 1)   # падение: лонг нельзя


def test_default_gate_also_hits_an_open_position():
    """Прежнее поведение: гейт закрывает открытую позицию (want=0 -> флип)."""
    assert gate(85000, 84000, TREND, in_position=True) == (1, 0)


def test_entry_only_is_silent_in_position():
    p = {**TREND, "reg_entry_only": 1}
    assert gate(85000, 84000, p, in_position=True) == (1, 1)        # позицию не трогаем
    assert gate(85000, 84000, p, in_position=False) == (1, 0)       # вход гейтится


def test_dead_zone_silences_the_gate_entirely():
    p = {**TREND, "reg_entry_only": 1}
    assert gate(84005, 84000, p, in_position=False) == (1, 1)       # внутри полосы
    assert gate(84005, 84000, TREND, in_position=True) == (1, 1)


def test_axis_is_in_the_schema():
    keys = {s["key"] for s in REGISTRY["macd_shectory1"]["params_schema"]}
    assert "reg_entry_only" in keys
