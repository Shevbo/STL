"""Ретро-реверс: оценка обязана давать НОЛЬ там, где алгоритма нет.

Проект: docs/retro-reverse-design.md. Эти тесты закрывают главную ловушку задачи
— сырая доля совпадений льстит любому правилу, поэтому проверяется именно
нормировка: «нет умения» должно быть нулём, а не 55 или 80 процентами.
"""
import random
import statistics

from trader.lab.retro_reverse import (DOWN, FLAT, UP, Markov, accuracy,
                                      apply_threshold, fit, noise_floor,
                                      quantize, score, split_segments,
                                      survival, survival_segmented, walk)


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
            assert "noise_median" in r and "noise_median_max" in r, r
            assert r["verdict"] == "в пределах шума", r


def test_sign_mode_noise_floor_exceeds_shuffle_under_vol_clustering():
    """Кластеризация волатильности видна цепи без всякого знания направления:
    после блока крупных шагов следующий шаг редко «на месте», и mode="sign"
    (сохраняет |приращения|, знак случаен) обязан давать более высокий уровень
    шума, чем mode="shuffle" (рушит и порядок величин)."""
    rng = random.Random(13)
    px = [100.0]
    for i in range(6000):
        sigma = 20.0 if (i // 200) % 2 == 0 else 1.0   # блоки высокой/низкой волатильности
        px.append(px[-1] + rng.gauss(0, sigma))
    sign_floor = noise_floor(px, 400, 150, k=3, draws=5, seed=1, mode="sign")
    shuffle_floor = noise_floor(px, 400, 150, k=3, draws=5, seed=1, mode="shuffle")
    sign_med = statistics.median(x for draw in sign_floor for x in draw)
    shuffle_med = statistics.median(x for draw in shuffle_floor for x in draw)
    assert sign_med > shuffle_med, (sign_med, shuffle_med)


def test_split_segments_cuts_on_gap_over_threshold():
    """Разрыв > 3 суток между соседними барами — граница сегмента: walk() режет
    окно по числу баров, а не по времени, и иначе перескочит экспирационную дыру."""
    day = 86400
    rows = [[i * 60, 1, 1, 1, 1, 1] for i in range(5)]                  # сегмент 1: 5 баров
    gap_start = rows[-1][0] + 4 * day                                   # разрыв 4 суток > порога
    rows += [[gap_start + i * 60, 1, 1, 1, 1, 1] for i in range(3)]     # сегмент 2: 3 бара
    segs = split_segments(rows, gap_days=3.0)
    assert [len(s) for s in segs] == [5, 3]


def test_split_segments_keeps_gap_under_threshold_together():
    """Разрыв в пределах порога (обычные ночь/выходные) ряд не режет."""
    day = 86400
    rows = [[0, 1, 1, 1, 1, 1], [2 * day, 1, 1, 1, 1, 1], [2 * day + 60, 1, 1, 1, 1, 1]]
    segs = split_segments(rows, gap_days=3.0)
    assert len(segs) == 1 and len(segs[0]) == 3


def test_split_segments_empty_input():
    assert split_segments([]) == []


def test_survival_segmented_merges_distributions_before_stats():
    """Оценки и шум ДВУХ сегментов объединяются до подсчёта медианы/перцентиля —
    не считаются как две отдельные строки отчёта."""
    rng = random.Random(9)
    seg = [100.0]
    for _ in range(3000):
        seg.append(seg[-1] + rng.gauss(0, 10))
    rows = survival_segmented([seg, seg], weeks=(1,), bars_per_week=500,
                              test_weeks=1, k=2, draws=3)
    single = survival(seg, weeks=(1,), bars_per_week=500, test_weeks=1, k=2, draws=3)
    assert rows[0]["n"] == 2 * single[0]["n"]
