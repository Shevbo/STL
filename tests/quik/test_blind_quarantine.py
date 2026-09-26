"""Карантин после слепоты: вход, рождённый в дыре, не исполняется сразу.

25.09.2026 связь вернулась после трёх часов тишины, и через одиннадцать минут
сторож купил 20 контрактов по уровню, пройденному в дыре."""

from trader.quik.blind_quarantine import BLIND_SEC, QUARANTINE_SEC, BlindQuarantine, holds

NOW = 1_790_000_000_000
S = 1000


class _SO:
    def __init__(self, parent_id=""):
        self.parent_id = parent_id
        self.so_id = "x"


def test_continuous_data_never_raises_quarantine():
    q = BlindQuarantine()
    for i in range(10):
        q.observe(True, NOW + i * 5 * S)
    assert q.active(NOW + 50 * S) is False


def test_gap_in_data_holds_entries_after_the_link_returns():
    q = BlindQuarantine()
    q.observe(True, NOW)
    back = NOW + (BLIND_SEC + 60) * S          # данные пропали и вернулись
    q.observe(True, back)
    assert q.active(back) is True
    assert q.gap_sec >= BLIND_SEC
    assert q.left_sec(back) == QUARANTINE_SEC
    # По истечении карантина заявки снова стреляют.
    assert q.active(back + (QUARANTINE_SEC + 1) * S) is False


def test_short_hiccup_is_not_blindness():
    q = BlindQuarantine()
    q.observe(True, NOW)
    q.observe(True, NOW + (BLIND_SEC - 10) * S)
    assert q.active(NOW + (BLIND_SEC - 10) * S) is False


def test_stale_frames_do_not_restart_the_clock():
    """Пока кадр мёртвый, «видели» не засчитывается: иначе дыра не обнаружится."""
    q = BlindQuarantine()
    q.observe(True, NOW)
    for i in range(1, 6):
        q.observe(False, NOW + i * 60 * S)     # данных нет
    back = NOW + 6 * 60 * S
    q.observe(True, back)
    assert q.active(back) is True


def test_protective_orders_are_never_held():
    """Задержать вход — потерять возможность; задержать выход — оставить
    позицию голой, и это дороже."""
    assert holds(_SO()) is True                    # вход держим
    assert holds(_SO(parent_id="parent")) is False  # защитную — никогда


def test_operator_can_clear_it_early():
    q = BlindQuarantine()
    q.observe(True, NOW)
    back = NOW + (BLIND_SEC + 5) * S
    q.observe(True, back)
    assert q.active(back) is True
    q.clear()
    assert q.active(back) is False
