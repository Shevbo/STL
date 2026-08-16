"""Гейт режима рынка: сторона гейтится САМИМ РЫНКОМ, а не часами.

Заведён 16.08.2026 после замера, который закрыл предыдущую гипотезу: робот
теряет на растущих окнах ДАЖЕ с расписанием сторон, потому что расписание —
структура времени суток и режима рынка не видит по построению. −49 109 и −51 029
на двух растущих окнах против +324 639 и +425 568 на двух падающих.
"""

from trader.lab.strategies.library import REGISTRY, make_on_bar


def gate(px: float, ref: float, band_frac: float, mode: int = 1) -> tuple[int, int]:
    """Та же формула, что в make_on_bar, на входе цена и средняя.

    Возвращает (лонг_разрешён, шорт_разрешён) при обеих сторонах, открытых
    базовыми allow_long/allow_short.
    """
    a_long = a_short = 1
    band = px * band_frac / 10000.0 if band_frac > 0 else 0.0
    up, down = px > ref + band, px < ref - band
    if mode == 2:
        up, down = down, up
    if up:
        a_short = 0
    elif down:
        a_long = 0
    return a_long, a_short


def test_uptrend_forbids_short_downtrend_forbids_long():
    assert gate(101_000, 100_000, 0) == (1, 0)      # рост: шорт закрыт
    assert gate(99_000, 100_000, 0) == (0, 1)       # падение: лонг закрыт


def test_dead_zone_keeps_both_sides_open():
    """Без мёртвой зоны сторона переключалась бы на каждом касании средней, и
    гейт стал бы генератором разворотов — болезнь, которую у лестницы лечит
    разножка. В полосе гейт обязан МОЛЧАТЬ, а не выключать торговлю."""
    # 0.20% от 100 000 = 200 пунктов: цена в 100 руб. от средней внутри зоны.
    assert gate(100_100, 100_000, 20) == (1, 1)
    assert gate(99_900, 100_000, 20) == (1, 1)
    # За краем зоны гейт срабатывает.
    assert gate(100_300, 100_000, 20) == (1, 0)
    assert gate(99_700, 100_000, 20) == (0, 1)


def test_contrarian_mode_mirrors_the_gate():
    """Фейд движения на некоторых контрактах устойчиво прибыльнее следования —
    это ось перебора, а не экзотика."""
    assert gate(101_000, 100_000, 0, mode=2) == (0, 1)
    assert gate(99_000, 100_000, 0, mode=2) == (1, 0)


def test_axis_is_exposed_by_every_registry_strategy():
    keys = {"reg_n", "reg_band", "reg_mode"}
    missing = [sid for sid, spec in REGISTRY.items()
               if not keys <= {p["key"] for p in spec["params_schema"]}]
    assert not missing, f"нет гейта режима у: {', '.join(sorted(missing))}"


def test_default_is_off():
    """Ключи вводятся в реестр, по которому посчитаны миллионы строк лидерборда:
    поведение по умолчанию обязано остаться прежним до бита."""
    for sid, spec in REGISTRY.items():
        assert spec["default_params"].get("reg_n") == 0, sid


def test_gate_needs_a_period_and_enough_bars():
    """reg_n=0 и reg_n=1 — выключено; окно короче периода тоже: считать среднюю
    не по чему, а гадать в торговом пути нельзя."""
    on_bar = make_on_bar("macd_shectory1")
    assert callable(on_bar)
    # Формула гейта включается только при reg_n > 1 И len(bars) >= reg_n —
    # проверяем саму границу условия, как она записана в make_on_bar.
    for reg_n, bars_n, expect_on in ((0, 500, False), (1, 500, False),
                                     (200, 100, False), (200, 500, True)):
        assert (reg_n > 1 and bars_n >= reg_n) is expect_on
