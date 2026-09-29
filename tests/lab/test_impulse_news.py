"""Детектор импульсов и пересечений средних на синтетике (trader/lab/impulse_news.py),
плюс контроль/исключения из scripts/impulse_news_report.py (не пакет - грузим по пути)."""
import importlib.util
import os

from trader.lab import impulse_news as im

_report_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts",
                            "impulse_news_report.py")
_spec = importlib.util.spec_from_file_location("impulse_news_report", _report_path)
inr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inr)

T0 = 1_750_000_000


def _bars(closes, vols=None):
    vols = vols or [100.0] * len(closes)
    return [[T0 + 60 * i, c, c, c, c, v] for i, (c, v) in enumerate(zip(closes, vols))]


def _noise(n):
    # пила +-1: медиана 5-барного хода = 1, объём ровный
    return [1000.0 + (i % 2) for i in range(n)]


def _jump(closes, vols, at, size):
    for i in range(at, len(closes)):
        closes[i] += size
    vols[at] = 5000.0


def _spike(closes, vols, at, size):
    """Одна свеча (не насовсем, в отличие от _jump): ровно одно срабатывание, без
    эха разворота 5 баров спустя - удобно для тестов, где нужна ровно одна точка."""
    closes[at] += size
    vols[at] = 5000.0


def test_no_impulses_on_flat_noise():
    assert im.detect(_bars(_noise(3000))) == []


def test_single_jump_gives_one_event_with_right_start():
    c, v = _noise(3000), [100.0] * 3000
    _jump(c, v, 2000, 50)
    ev = im.detect(_bars(c, v))
    assert len(ev) == 1
    assert ev[0]["start_idx"] == 1995 and ev[0]["start_time"] == T0 + 60 * 1995
    assert ev[0]["dir"] == 1


def test_jumps_3_minutes_apart_merge():
    c, v = _noise(3000), [100.0] * 3000
    _jump(c, v, 2000, 50)
    _jump(c, v, 2003, 50)
    ev = im.detect(_bars(c, v))
    assert len(ev) == 1 and ev[0]["start_idx"] == 1995


def test_jump_without_volume_is_not_impulse():
    c = _noise(3000)
    for i in range(2000, 3000):
        c[i] += 50
    assert im.detect(_bars(c)) == []


def test_continuation_is_signed_by_direction():
    times = [T0 + 60 * i for i in range(100)]
    closes = [100.0 + i for i in range(100)]          # растёт на 1 пункт в минуту
    up = im.continuation(times, closes, 10, 1, 2.0)
    down = im.continuation(times, closes, 10, -1, 2.0)
    assert up == {5: 2.5, 15: 7.5, 60: 30.0}
    assert down == {5: -2.5, 15: -7.5, 60: -30.0}
    assert im.continuation(times, closes, 90, 1, 2.0)[60] is None


def test_crosses_direction():
    gap = [None, -1.0, -0.5, 0.0, 0.3, 0.2, -0.1]
    assert im.crosses(gap) == [(4, 1), (6, -1)]


def test_m15_bars_close_on_last_minute_of_quarter():
    base = 1_750_000_500 - 1_750_000_500 % 900          # начало часа кратно 15 минутам
    times = [base + 60 * i for i in range(40)]
    # последние минуты четвертей: 14 и 29; хвост 30..39 незавершён и не берётся
    assert im.tf_ends(times, 15) == [14, 29]
    assert im.tf_ends(times[:3], 1) == [0, 1, 2]


def test_event_volume_multiples():
    c, v = _noise(3000), [100.0] * 3000
    _jump(c, v, 2000, 50)
    e = im.detect(_bars(c, v))[0]
    assert e["max_min_vol_x"] == 50.0            # 5000 против медианы 100
    assert e["vol_x"] == 10.8                    # 5400 против медианы 5-барных сумм 500


def test_continuation_uses_first_trigger_not_chain_end():
    """Bugfix 29.09.2026: продолжение считалось от конца цепочки (известен только
    через 5 баров после последнего срабатывания - заглядывание). Два срабатывания в
    3 минутах сливаются в одну цепочку; корректный анкер продолжения - ПЕРВОЕ
    срабатывание, а не конец цепочки - от них должны получаться разные числа."""
    n = 3000
    c, v = _noise(n), [100.0] * n
    _jump(c, v, 2000, 50)
    _jump(c, v, 2003, 30)              # второе срабатывание той же цепочки, 3 мин позже
    bars = _bars(c, v)
    times, closes, vols = im._cols(bars)

    raw = im.detect(bars)
    assert len(raw) == 1
    e = raw[0]
    assert e["n_det"] > 1                          # цепочка, не одиночное срабатывание
    t0 = e["start_idx"] + 5

    res = im.analyze(bars, T0, T0 + 60 * (n - 1))
    ev = res["events"][0]

    move, _, med_m, _ = im.medians(closes, vols)
    exp = im.continuation(times, closes, t0, 1 if move[t0] > 0 else -1, med_m[t0])
    assert ev["f5"] == exp[5]
    assert ev["f15"] == exp[15]
    assert ev["f60"] == exp[60]

    # От конца цепочки число другое - фиксируем сам факт различия, чтобы тест падал,
    # если анкер продолжения снова тихо сползёт на конец цепочки.
    end_val = im.continuation(times, closes, e["end_idx"],
                              1 if move[e["end_idx"]] > 0 else -1, med_m[e["end_idx"]])
    assert end_val[5] != ev["f5"]


def test_gap0_flags_a_hole_in_the_first_triggers_window():
    """gap0 - время между баром t-5 и баром t первого срабатывания; отчёт (item 3)
    исключает событие, если оно больше 10 минут. Здесь проверяем само число.
    _spike (не _jump): ровно одно срабатывание, без эха разворота 5 баров спустя,
    иначе сдвиг времени ниже задел бы ещё и его окно, а не только первое."""
    c, v = _noise(3000), [100.0] * 3000
    _spike(c, v, 2000, 50.0)
    normal = im.detect(_bars(c, v))
    assert len(normal) == 1
    assert normal[0]["gap0"] == 300                # 5 минутных баров подряд, без дыр

    bars = _bars(c, v)
    for row in bars[1998:]:                        # дыра 15 минут внутри окна t-5..t
        row[0] += 900
    gapped = im.detect(bars)
    assert len(gapped) == 1
    assert gapped[0]["gap0"] > 600


def test_shift_control_steps_are_multiples_of_seven():
    """Bugfix: сдвиги 200/270/153 и т.п. ломали день недели (не кратны 7). Контроль
    сдвинутой ленты в отчёте обязан брать только кратные 7 (день недели сохраняется)."""
    assert len(inr.SHIFT_STEPS) == 10
    assert all(k % 7 == 0 for k in inr.SHIFT_STEPS)
    assert inr.SHIFT_STEPS == tuple(range(7, 71, 7))


def test_split_by_gap_excludes_gap_and_roll_separately():
    evs = [
        {"gap0": 300},                              # чистое, 5 минут подряд
        {"gap0": 601},                               # только что за 10-минутным порогом
        {"gap0": 3 * 3600},                          # ночной перерыв, не ролл
        {"gap0": 2 * 86400},                          # дыра ролла
    ]
    clean, gap_n, roll_n = inr.split_by_gap(evs)
    assert clean == [evs[0]]
    assert gap_n == 2
    assert roll_n == 1
