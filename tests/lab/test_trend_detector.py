"""Детектор режима: три состояния должны РАЗЛИЧАТЬСЯ, а не совпадать по названию.

Тест пинит ровно те способы соврать, которые у такого детектора бывают:
чистый тренд объявить боковиком, боковик с большим размахом объявить трендом,
один рывок в конце окна выдать за тренд всего окна, и — главное — дать разный
ответ на одной и той же дороге, снятой с разной частотой.
"""
import math
import random

from trader.lab.trend_detector import FALL, FLAT, RISE, _er, detect_regime


def _series(kind: str, n: int = 400) -> list[float]:
    rnd = random.Random(7)
    if kind == "up":
        return [100 * (1.0006 ** i) + rnd.uniform(-0.2, 0.2) for i in range(n)]
    if kind == "down":
        return [100 * (0.9994 ** i) + rnd.uniform(-0.2, 0.2) for i in range(n)]
    if kind == "flat":
        return [100 + math.sin(i / 9) * 1.5 + rnd.uniform(-0.4, 0.4) for i in range(n)]
    if kind == "wide":      # размах большой, хода нет
        return [100 + math.sin(i / 40) * 12 + rnd.uniform(-1, 1) for i in range(n)]
    if kind == "spike":     # весь ход одним рывком в конце окна
        head = [100 + rnd.uniform(-0.3, 0.3) for _ in range(int(n * 0.85))]
        return head + [100 + (i + 1) * 0.12 for i in range(n - len(head))]
    raise ValueError(kind)


def test_three_states_are_distinguished():
    assert detect_regime(_series("up")).state == RISE
    assert detect_regime(_series("down")).state == FALL
    assert detect_regime(_series("flat")).state == FLAT


def test_wide_range_without_drift_is_flat():
    """Качели на 12% размаха, вернувшиеся в исходную точку, — это боковик.
    Детектор, смотрящий только на размах, назвал бы их трендом."""
    r = detect_regime(_series("wide"))
    assert r.state == FLAT, r.as_dict()


def test_confidence_falls_when_move_is_one_late_spike():
    """Ход есть, но сделан в последней части окна: включать под него стратегию
    на весь фрейм нельзя, и уверенность обязана это показать."""
    steady = detect_regime(_series("up"))
    spike = detect_regime(_series("spike"))
    assert steady.confidence == 1.0
    assert spike.confidence < 0.5, spike.as_dict()


def test_answer_survives_resampling():
    """Одна и та же дорога, снятая вдвое чаще, — тот же режим и почти тот же ER.
    Без прореживания ER падает с числом точек, и порог, откалиброванный на одном
    окне, врал бы на другом."""
    up = _series("up")
    dense = [v for c in up for v in (c, c)]
    a, b = detect_regime(up), detect_regime(dense)
    assert a.state == b.state
    assert abs(a.er - b.er) < 0.05


def test_efficiency_ratio_separates_road_from_zigzag():
    assert _er(_series("up")) > 0.4 > _er(_series("flat"))


def test_degenerate_input_does_not_crash():
    for bad in ([], [100.0], [0.0, 0.0]):
        assert detect_regime(bad).state == FLAT
