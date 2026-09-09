"""Rich Fool — предоткрытийная лестница пробоя. Проверяем ФАКТ сделок робота на
синтетических днях, а не наличие веток в коде.

Что легко сломать и что здесь закреплено:
  1. Уровни считаются от вчерашнего закрытия ± amp·dist_pct, amp = средний дневной
     размах за n_days завершённых дней.
  2. Первый пробой запирает сторону; встречная сторона после этого мертва.
  3. Исполнение только в окне [открытие, открытие+hold_min]; вне окна и без
     позиции робот разоружается («снимает заявки»).
  4. invert=1 переворачивает сторону сделки, оставляя детекцию пробоя на месте.
  5. Запрещённая сторона гасит вход.
  6. Открытая позиция ведётся только по TP/SL и НЕ закрывается в конце дня.
"""
import asyncio

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.rich_fool import on_bar

SYM = "RIU6"
D0 = 1788307200                       # среда 00:00 UTC (= МСК-стенка), день -2
DAY = 86400
OPEN_HM = 7 * 60                      # 07:00 МСК, открытие утренней сессии FORTS


def _bar(day_epoch: int, minute: int, o, h, low, c) -> Bar:
    return Bar(time=day_epoch + minute * 60, open=o, high=h, low=low, close=c, volume=100)


def _prior_day(day_epoch: int, lo: float, hi: float, last_close: float) -> list[Bar]:
    """Несколько баров с заданным дневным размахом [lo, hi] и закрытием last_close."""
    mid = (lo + hi) / 2
    return [
        _bar(day_epoch, OPEN_HM + 0, mid, hi, mid, hi),
        _bar(day_epoch, OPEN_HM + 1, hi, hi, lo, lo),
        _bar(day_epoch, OPEN_HM + 2, lo, mid, lo, last_close),
    ]


def _bars(day2_tail: list[Bar]) -> list[Bar]:
    # day -2: размах 90..110 (=20); day -1: 95..105 (=10), закрытие 100.
    # amp за 2 дня = 15. dist_pct=50 -> base 7.5 -> up[0]=107.5 dn[0]=92.5
    # step_gap_pct=20 -> gap 3.0 -> up[1]=110.5 dn[1]=89.5 ; sl_pct=40 -> R=6.0
    b = _prior_day(D0, 90.0, 110.0, 100.0) + _prior_day(D0 + DAY, 95.0, 105.0, 100.0)
    b.append(_bar(D0 + 2 * DAY, OPEN_HM - 1, 100.0, 100.1, 99.9, 100.0))  # арм-бар (вне окна)
    return b + day2_tail


PARAMS = {"symbol": SYM, "n_days": 2, "dist_pct": 50, "step_count": 2,
          "step_gap_pct": 20, "qty": 1, "sl_pct": 40, "rr_x10": 20,
          "open_hour": 7, "open_min": 0, "place_lead_min": 10, "hold_min": 30}


async def _drive(day2_tail: list[Bar], extra: dict):
    # хвост из спокойных баров, чтобы филл никогда не пришёлся на последний бар
    # (place_order читает _bars[cursor+1]).
    d2 = D0 + 2 * DAY
    pad = [_bar(d2 + DAY, m, 107.0, 107.2, 106.8, 107.0) for m in range(0, 20)]
    rt = BacktestRuntime(bars=_bars(day2_tail) + pad, symbol=SYM, initial_equity=1_000_000.0)
    p = {**PARAMS, **extra}
    while True:
        await on_bar(rt, p)
        if not rt.advance():
            break
    return [(o.side, int(o.qty), float(o.price)) for o in rt._orders]


def _run(day2_tail: list[Bar], **extra):
    return asyncio.run(_drive(day2_tail, extra))


def _up_break_then_run_up() -> list[Bar]:
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 108.0, 100.0, 107.6))   # пробой up[0]=107.5
    t.append(_bar(d2, OPEN_HM + 4, 107.6, 111.0, 107.6, 110.8))   # пробой up[1]=110.5 (долив)
    for i, m in enumerate(range(5, 45)):
        px = 111.0 + i * 1.0                                       # ползёт вверх -> лонг в тейк
        t.append(_bar(d2, OPEN_HM + m, px, px + 0.5, px - 0.3, px))
    return t


def test_up_breakout_enters_long_and_ladders_the_second_step():
    orders = _run(_up_break_then_run_up())
    assert orders, "должна быть хотя бы одна сделка"
    assert orders[0][0] == "buy", orders
    buys_before_first_sell = []
    for s, q, px in orders:
        if s == "sell":
            break
        buys_before_first_sell.append(px)
    assert len(buys_before_first_sell) == 2, f"две ступени лестницы, получили {buys_before_first_sell}"


def test_ladder_volume_doubles_each_step():
    orders = _run(_up_break_then_run_up(), vol_mult=20, step_count=3)
    qtys = []
    for s, q, _ in orders:
        if s == "sell":
            break
        qtys.append(q)
    assert qtys == [1, 2, 4], f"объёмы ступеней ×2: ожидали [1,2,4], получили {qtys}"


def _entry_prices(orders):
    px = []
    for s, q, p in orders:
        if s == "sell":
            break
        px.append(p)
    return px


def test_spacing_progression_shrinks_the_gap_further_out():
    # долгий проезд вверх, чтобы набрать все 5 ступеней
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 2)]
    for i, m in enumerate(range(2, 44)):
        px = 101.0 + i * 3.0
        tail.append(_bar(d2, OPEN_HM + m, px, px + 1.0, px - 0.5, px))
    lin = _entry_prices(_run(tail, step_count=5, spacing=0, step_gap_pct=25))
    prog = _entry_prices(_run(tail, step_count=5, spacing=1, span_pct=120))
    assert len(lin) == 5 and len(prog) == 5, (lin, prog)
    lin_gaps = [round(lin[i + 1] - lin[i], 3) for i in range(4)]
    prog_gaps = [round(prog[i + 1] - prog[i], 3) for i in range(4)]
    assert max(lin_gaps) - min(lin_gaps) < 1e-6, f"spacing=0 — равный шаг: {lin_gaps}"
    assert all(prog_gaps[i] > prog_gaps[i + 1] for i in range(3)), f"spacing=1 — шаг убывает: {prog_gaps}"


def test_up_breakout_inverted_is_a_short():
    plain = _run(_up_break_then_run_up())
    inv = _run(_up_break_then_run_up(), invert=1)
    assert plain[0][0] == "buy" and inv[0][0] == "sell", (plain[0], inv[0])


def test_forbidden_side_blocks_entry():
    orders = _run(_up_break_then_run_up(), allow_long=0)
    assert orders == [], f"лонги запрещены, пробой вверх не должен войти: {orders}"


def test_no_breakout_no_trade():
    d2 = D0 + 2 * DAY
    calm = [_bar(d2, OPEN_HM + m, 100.0, 101.0, 99.0, 100.0) for m in range(0, 45)]
    assert _run(calm) == [], "цена не дошла до уровней — сделок нет"


def test_breakout_after_window_is_ignored():
    d2 = D0 + 2 * DAY
    late = [_bar(d2, OPEN_HM + m, 100.0, 100.5, 99.5, 100.0) for m in range(0, 31)]
    late.append(_bar(d2, OPEN_HM + 35, 100.0, 108.0, 100.0, 107.6))   # пробой ПОСЛЕ окна (>30)
    late += [_bar(d2, OPEN_HM + m, 108.0, 109.0, 107.0, 108.0) for m in range(36, 45)]
    assert _run(late) == [], "пробой за пределами hold_min не входит"


def test_position_is_not_flattened_at_end_of_day():
    """Пробой вверх в окне, потом цена стоит ниже тейка и выше стопа до конца дня.
    Позиция обязана ДОЖИТЬ до следующего дня, а не закрыться в 23:45."""
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 108.0, 100.0, 107.6))       # вход long ~107.5
    # до конца суток болтается в коридоре 105..109 (стоп ~101.5, тейк ~119.5 — ни один)
    for m in range(4, 16 * 60):
        t.append(_bar(d2, OPEN_HM + m, 107.0, 108.5, 105.5, 107.0))
    orders = _run(t)
    sells = [o for o in orders if o[0] == "sell"]
    assert orders and orders[0][0] == "buy", orders
    assert sells == [], f"позицию закрыли до TP/SL: {sells}"
