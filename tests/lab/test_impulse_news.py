"""Детектор импульсов и пересечений средних на синтетике (trader/lab/impulse_news.py)."""
from trader.lab import impulse_news as im

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
