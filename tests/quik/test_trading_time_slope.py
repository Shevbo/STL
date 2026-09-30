"""Наклонная граница живёт в ТОРГОВОМ времени, а не в календарном.

Оператор поймал это 30.09.2026 на треугольнике RTS: линию он проводит по
графику, где ночи, клиринга и выходного на оси X нет вовсе. Тот же наклон,
посчитанный по календарю, даёт ДРУГУЮ прямую — тем более пологую, чем больше
перерывов попало между точками; через ночь ошибка кратная.
"""
from trader import market_session as ms
from trader.quik.smart_orders import SmartOrder, corridor_bounds, new_id

D1 = 1_790_740_800_000          # 30.09.2026 07:00 МСК
H = 3_600_000

# Расписание: два торговых дня по 4 часа (07:00-11:00), между ними ночь в 20 часов.
SCHED = {"sessions": [(D1, D1 + 4 * H, "main_session"),
                      (D1 + 24 * H, D1 + 28 * H, "main_session")],
         "holidays": set()}


def _corr(**kw):
    base = dict(so_id=new_id(), kind="corridor", code="RIZ6", side="sell", qty=1,
                c_qty=1, c_t1_ms=D1, c_p1=85000.0, c_t2_ms=D1 + 2 * H,
                c_p2=84000.0, c_low=83000.0, created_ms=D1)
    base.update(kw)
    return SmartOrder(**base)


def test_inside_one_session_both_clocks_agree():
    """Внутри непрерывной сессии торговое время равно календарному."""
    so = _corr()
    assert corridor_bounds(so, D1 + H, SCHED)[1] == corridor_bounds(so, D1 + H)[1] == 84500.0


def test_across_the_night_calendar_clock_lies():
    """Через ночь календарь растягивает ось и делает линию пологой."""
    so = _corr()
    # торгового времени с начала прошло 4 ч (день 1) + 1 ч (день 2) = 5 ч,
    # то есть 2.5 шага по 2 часа: 85000 − 2.5*1000 = 82500
    assert corridor_bounds(so, D1 + 25 * H, SCHED)[1] == 82500.0
    # календарь насчитал бы 25 часов = 12.5 шагов и увёл линию на 72500
    assert corridor_bounds(so, D1 + 25 * H)[1] == 72500.0
    # разница в десять тысяч пунктов — это не погрешность, это другая фигура
    assert abs(corridor_bounds(so, D1 + 25 * H, SCHED)[1]
               - corridor_bounds(so, D1 + 25 * H)[1]) == 10_000.0


def test_night_itself_does_not_move_the_line():
    """Пока биржа стоит, линия стоит тоже: на графике этих баров просто нет."""
    so = _corr()
    at_close = corridor_bounds(so, D1 + 4 * H, SCHED)[1]
    at_midnight = corridor_bounds(so, D1 + 12 * H, SCHED)[1]
    at_open = corridor_bounds(so, D1 + 24 * H, SCHED)[1]
    assert at_close == at_midnight == at_open


def test_trading_ms_between_counts_only_sessions():
    assert ms.trading_ms_between(SCHED, D1, D1 + 2 * H) == 2 * H
    assert ms.trading_ms_between(SCHED, D1, D1 + 25 * H) == 5 * H
    assert ms.trading_ms_between(SCHED, D1 + 25 * H, D1) == -5 * H, "знак сохраняется"
    assert ms.trading_ms_between(SCHED, D1 + 5 * H, D1 + 6 * H) == 0, "ночь не считается"
    assert ms.trading_ms_between({}, D1, D1 + H) == 0, "нет расписания — нет времени"
