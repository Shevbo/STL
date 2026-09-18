"""Сигнал shectory_3ema: три EMA по порядку — тренд, иначе НЕЙТРАЛЬ.

Заведено 18.09.2026 по запросу оператора (проверка оси «не выходить по
перевороту» на семействе, устроенном иначе, чем MACD). Отличие от 2EMA не в
числе линий, а в нейтральной зоне: у двух линий сигнал есть ВСЕГДА (одна из них
выше), поэтому каждое пересечение переворачивает позицию; тройка при спутанных
линиях возвращает None и позиция не трогается.
"""
from trader.lab.runtime import Bar
from trader.lab.strategies.library import REGISTRY, sig_3ema


def bars(closes):
    return [Bar(i * 60, c, c, c, c, 1) for i, c in enumerate(closes)]


P = {"ema1": 3, "ema2": 5, "ema3": 9}


def test_rising_series_is_long():
    assert sig_3ema(bars([100 + i for i in range(60)]), P) == 1


def test_falling_series_is_short():
    assert sig_3ema(bars([200 - i for i in range(60)]), P) == -1


def test_tangled_lines_give_no_signal():
    """Разворот на свежем пике: быстрая уже ниже средней, медленная ещё выше —
    линии не выстроены, сигнала нет (в make_on_bar None = держим позицию)."""
    up = [100 + i for i in range(40)]
    assert sig_3ema(bars(up + [134]), P) is None          # первый удар вниз: линии спутаны
    assert sig_3ema(bars(up + [134, 124]), P) == -1       # продолжение — порядок построен


def test_equal_periods_are_degenerate():
    # Совпавшие периоды: ряды тождественны, порядок не построить (см. sig_2ema).
    assert sig_3ema(bars([100 + i for i in range(60)]), {"ema1": 5, "ema2": 5, "ema3": 9}) is None


def test_registry_entry():
    r = REGISTRY["shectory_3ema"]
    assert r["default_params"]["ema1"] == 10 and r["default_params"]["ema3"] == 140
    assert r["warmup"]({"ema1": 10, "ema2": 40, "ema3": 140}) == 560   # 4 x самой длинной
