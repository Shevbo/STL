"""Ретро-реверс: оценка обязана давать НОЛЬ там, где алгоритма нет.

Проект: docs/retro-reverse-design.md. Эти тесты закрывают главную ловушку задачи
— сырая доля совпадений льстит любому правилу, поэтому проверяется именно
нормировка: «нет умения» должно быть нулём, а не 55 или 80 процентами.
"""
import random

from trader.lab.retro_reverse import (DOWN, FLAT, UP, Markov, accuracy,
                                      apply_threshold, fit, quantize, score,
                                      survival, walk)


def test_quantize_threshold_comes_from_the_window():
    """Порог считается по окну, а не задаётся руками: иначе доли флэтов
    несравнимы между инструментами разного масштаба."""
    sym, thr = quantize([100, 110, 100, 110, 100], thr_frac=0.5)
    assert thr == 5.0 and sym == [UP, DOWN, UP, DOWN]


def test_apply_threshold_does_not_peek():
    """К окну проверки применяется порог ОКНА ПОДГОНКИ, без пересчёта."""
    assert apply_threshold([100, 101, 90], thr=5.0) == [FLAT, DOWN]


def test_deterministic_series_is_recognised():
    saw, px = [], 100.0
    for i in range(2000):
        px += (10.0 if i % 4 in (0, 1) else -10.0)
        saw.append(px)
    s = walk(saw, 400, 150, k=3)
    assert s and min(s) > 90, s[:3]


def test_pure_noise_scores_near_zero():
    rng = random.Random(7)
    px = [100.0]
    for _ in range(3000):
        px.append(px[-1] + rng.gauss(0, 10))
    s = walk(px, 400, 150, k=2)
    assert sum(s) / len(s) < 20, sum(s) / len(s)


def test_trend_is_not_mistaken_for_an_algorithm():
    """«Всегда вверх» на растущем ряде: сырая доля дала бы 55-60%, оценка — ноль."""
    rng = random.Random(3)
    px = [100.0 + 0.5 * i + rng.gauss(0, 5) for i in range(3000)]
    s = walk(px, 400, 150, k=0)
    assert sum(s) / len(s) < 20, sum(s) / len(s)


def test_flat_dominated_series_scores_zero():
    """Широкий порог делает почти всё флэтом: доля совпадений 95%, оценка ноль."""
    rng = random.Random(11)
    px = [100.0]
    for _ in range(3000):
        px.append(px[-1] + rng.gauss(0, 0.01))
    s = walk(px, 400, 150, k=1, thr_frac=50.0)
    assert max(s) == 0.0, max(s)


def test_score_is_zero_when_worse_than_base():
    """Алгоритм хуже константы = отсутствие алгоритма, а не отрицательный балл."""
    model = Markov(0, {}, DOWN)
    assert score(model, [UP] * 50, base_symbol=UP) == 0.0


def test_accuracy_needs_unseen_data():
    model = fit([UP, UP, DOWN, UP, UP, DOWN], k=2)
    assert 0.0 <= accuracy(model, [UP, UP, DOWN, UP]) <= 1.0
    assert accuracy(model, [UP]) == 0.0      # короче порядка модели


def test_survival_reports_noise_floor():
    """Выживаемость обязана возвращать уровень шума: без него оценку читать нельзя."""
    rng = random.Random(5)
    px = [100.0]
    for _ in range(9000):
        px.append(px[-1] + rng.gauss(0, 10))
    rows = survival(px, weeks=(1, 2), bars_per_week=1500, test_weeks=1, k=2, draws=3)
    assert rows and all("noise_p95" in r for r in rows if r.get("n")), rows
    for r in rows:
        if r.get("n"):
            assert r["verdict"] == "в пределах шума", r
