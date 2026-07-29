"""Оценка эффекта фильтров входа: фантом живёт до СВОЕГО выхода, а не час.

Инцидент 29.07.2026: оператор возразил на фиксированный часовой горизонт —
робот держит позицию до собственного сигнала выхода, иногда сутками, поэтому
часовой срез мерил не то. Здесь закреплено новое правило.
"""
from trader.lab.strategies.library import SKIP_HORIZON_SEC, settle_skip_phantoms

NOW = 1_785_000_000


def ph(p, d=1, q=1, t=NOW):
    return {"p": p, "d": d, "q": q, "t": t}


def test_holds_until_take_profit():
    """Пока тейк не достигнут — фантом ЖИВ, даже если прошли часы."""
    saved, keep = settle_skip_phantoms([ph(100.0)], price=101.0, atrv=1.0, tp=5.0,
                                       want=1, now_ts=NOW + 6 * 3600)
    assert saved == 0.0 and len(keep) == 1        # ход 1.0 < тейка 5.0 -> держим


def test_take_profit_closes_and_counts_as_lost_profit():
    """Отсеяли вход, который дошёл бы до тейка -> фильтр НЕДОзаработал (минус)."""
    saved, keep = settle_skip_phantoms([ph(100.0, q=2)], price=105.0, atrv=1.0, tp=5.0,
                                       want=1, now_ts=NOW + 60)
    assert keep == [] and saved == -10.0           # (105-100)*2 = +10 сделке = -10 эффекту


def test_signal_flip_closes_at_current_price():
    """Разворот сигнала = робот закрыл бы позицию здесь."""
    saved, keep = settle_skip_phantoms([ph(100.0)], price=97.0, atrv=1.0, tp=5.0,
                                       want=-1, now_ts=NOW + 60)
    assert keep == [] and saved == 3.0             # убыточный вход -> фильтр сберёг 3


def test_short_phantom_is_mirrored():
    saved, keep = settle_skip_phantoms([ph(100.0, d=-1)], price=95.0, atrv=1.0, tp=5.0,
                                       want=-1, now_ts=NOW + 60)
    assert keep == [] and saved == -5.0            # шорт от 100 до 95 = +5 сделке


def test_hard_timeout_is_a_safety_net_not_a_model():
    """Предохранитель: висящий фантом закрывается через горизонт, а не через час."""
    saved, keep = settle_skip_phantoms([ph(100.0)], price=100.5, atrv=1.0, tp=99.0,
                                       want=None, now_ts=NOW + 3600)
    assert len(keep) == 1                          # час НЕ закрывает
    saved, keep = settle_skip_phantoms([ph(100.0)], price=100.5, atrv=1.0, tp=99.0,
                                       want=None, now_ts=NOW + SKIP_HORIZON_SEC)
    assert keep == [] and saved == -0.5


def test_broken_record_never_breaks_stats():
    saved, keep = settle_skip_phantoms([{"мусор": 1}, ph(100.0)], price=100.0, atrv=1.0,
                                       tp=5.0, want=1, now_ts=NOW + 60)
    assert saved == 0.0 and len(keep) == 1
