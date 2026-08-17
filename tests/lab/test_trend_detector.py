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


def _densify(closes: list[float], factor: int, noise: float, seed: int = 3) -> list[float]:
    """Та же дорога, снятая в `factor` раз чаще: между точками линейная интерполяция
    ПЛЮС шум. Простое дублирование точек здесь не годится — приращения нулевые, длина
    пути не меняется, и тест прошёл бы даже с полностью удалённым прореживанием."""
    rnd = random.Random(seed)
    out = []
    for a, b in zip(closes, closes[1:]):
        for j in range(factor):
            t = j / factor
            out.append(a + (b - a) * t + rnd.uniform(-noise, noise))
    out.append(closes[-1])
    return out


def test_answer_survives_resampling():
    """Одна и та же дорога, снятая в 20 раз чаще и с шумом, — тот же режим.
    Без прореживания ER падает как 1/sqrt(n), и порог, честный на одном окне,
    объявил бы боковиком то же самое движение на другом."""
    up = _series("up")
    dense = _densify(up, 20, noise=0.25)
    a, b = detect_regime(up), detect_regime(dense)
    assert len(dense) > len(up) * 15
    assert a.state == b.state == RISE
    assert abs(a.er - b.er) < 0.2, (a.as_dict(), b.as_dict())


def test_random_walk_is_not_a_trend_on_any_window_length():
    """Шумовой пол ER равен 1/sqrt(n): на 20 точках это 0.22, на 120 — 0.09. Порог
    фиксированным числом означал бы на коротком окне «пропускай что угодно», и
    случайное блуждание разметилось бы трендом. Пин на обеих длинах."""
    for n in (20, 120):
        flats = 0
        for seed in range(12):
            rnd = random.Random(seed)
            px, walk = 100.0, []
            for _ in range(n):
                px *= 1 + rnd.gauss(0, 0.004)
                walk.append(px)
            flats += detect_regime(walk).state == FLAT
        assert flats >= 10, f"n={n}: блуждание объявлено трендом {12 - flats} раз из 12"


def test_efficiency_ratio_separates_road_from_zigzag():
    assert _er(_series("up")) > 0.4 > _er(_series("flat"))


def test_degenerate_input_does_not_crash():
    for bad in ([], [100.0], [0.0, 0.0]):
        assert detect_regime(bad).state == FLAT


def test_too_few_points_is_flagged_not_trusted():
    """На десяти точках порог ER равен 0.70, и ответ решается третьим знаком.
    Поймано на живых данных: рост RIU6 17-30.07 по дневным барам — «рост» с ER
    0.713 при пороге 0.696, а те же две недели по часовым и минутным — «боковик».
    Детектор обязан пометить такой ответ, а не выдавать его наравне с остальными."""
    up = _series("up", 400)
    thin_10 = up[::40]
    r = detect_regime(thin_10)
    assert r.enough is False and r.confidence == 0.0, r.as_dict()
    assert detect_regime(up).enough is True
