"""Rich Fool — предоткрытийная лестница пробоя. Проверяем ФАКТ сделок робота на
синтетических днях, а не наличие веток в коде.

Что легко сломать и что здесь закреплено:
  1. Уровни по формуле оператора: price(0)=prev_close, price(n)=price(n-1)+amp/(n+1),
     amp = средний дневной размах за n_days завершённых дней (зазоры amp/2, amp/3, …).
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
    # amp за 2 дня = 15. Формула price(n)=price(n-1)+amp/(n+1):
    #   up[0]=100+15/2=107.5, up[1]=107.5+15/3=112.5 ; sl_pct=40 -> R=6.0
    b = _prior_day(D0, 90.0, 110.0, 100.0) + _prior_day(D0 + DAY, 95.0, 105.0, 100.0)
    b.append(_bar(D0 + 2 * DAY, OPEN_HM - 1, 100.0, 100.1, 99.9, 100.0))  # арм-бар (вне окна)
    return b + day2_tail


PARAMS = {"symbol": SYM, "n_days": 2, "step_count": 2,
          "qty": 1, "sl_pct": 40, "rr_x10": 20,
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


def test_harmonic_gaps_shrink_further_out():
    # формула оператора: price(n)=price(n-1)+amp/(n+1), D=amp. Зазоры amp/2, amp/3, …
    # amp=15, prev_close=100 → уровни 107.5, 112.5, 116.25, 119.25, 121.75 (зазоры 5, 3.75, 3, 2.5).
    # Ползём на 0.25 (open точно попадает в уровень — филл без гэпа), окно расширяем до 120 мин.
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 2)]
    for i, m in enumerate(range(2, 100)):
        px = 100.0 + i * 0.25
        tail.append(_bar(d2, OPEN_HM + m, px, px + 0.1, px - 0.1, px + 0.1))
    px = _entry_prices(_run(tail, step_count=5, hold_min=120))
    assert px == [107.5, 112.5, 116.25, 119.25, 121.75], px
    gaps = [round(px[i + 1] - px[i], 3) for i in range(4)]
    assert gaps == [5.0, 3.75, 3.0, 2.5], gaps
    assert all(gaps[i] > gaps[i + 1] for i in range(3)), f"зазоры строго убывают: {gaps}"


def test_max_contracts_caps_the_ladder():
    # vol_mult=20, step_count=5 -> объёмы 1,2,4,8,16 (сумма 31). Потолок 6 -> добор
    # останавливается, суммарная позиция не превышает 6.
    orders = _run(_up_break_then_run_up(), step_count=5, vol_mult=20, max_contracts=6)
    signed = 0
    peak = 0
    for s, q, _ in orders:
        signed += q if s == "buy" else -q
        peak = max(peak, abs(signed))
    assert peak <= 6, f"позиция превысила потолок max_contracts=6: пик {peak}"


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


def _one_step_entry_then(tail: list[Bar]) -> list[Bar]:
    """Одна ступень (step_count=1) -> avg=107.5, R=6.0, стоп 101.5, тейк 119.5."""
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 108.0, 100.0, 107.6))   # вход long по up[0]=107.5
    return t + tail


def _exit_price(orders):
    sells = [px for s, _, px in orders if s == "sell"]
    assert sells, f"выхода не было: {orders}"
    return sells[-1]


def test_take_profit_fills_at_the_exact_level():
    """Тейк исполняется ПО ЦЕНЕ УРОВНЯ (119.5), а не по открытию следующего бара."""
    d2 = D0 + 2 * DAY
    tail = []
    for i, m in enumerate(range(4, 16)):
        px = 108.0 + i * 1.0                       # ползём вверх, тейк 119.5 пробьём хвостом бара
        tail.append(_bar(d2, OPEN_HM + m, px, px + 0.5, px - 0.3, px))
    orders = _run(_one_step_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 119.5, orders


def test_stop_loss_fills_at_the_exact_level():
    """Стоп исполняется ПО ЦЕНЕ УРОВНЯ (101.5), а не по отскоку на следующем баре."""
    d2 = D0 + 2 * DAY
    tail = []
    for i, m in enumerate(range(4, 11)):
        px = 107.0 - i * 1.0                       # сползаем вниз до стопа 101.5
        tail.append(_bar(d2, OPEN_HM + m, px, px + 0.3, px - 0.5, px))
    orders = _run(_one_step_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 101.5, orders


def test_gap_through_the_stop_fills_at_the_open_not_at_the_level():
    """Бар открылся СКВОЗЬ стоп — выход по открытию (99.0), т.е. с проскальзыванием.
    Ровно тот случай, где филл «по уровню» рисовал бы результат лучше реального."""
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + 4, 99.0, 99.5, 98.0, 98.5)]        # гэп ниже стопа 101.5
    tail += [_bar(d2, OPEN_HM + m, 99.0, 99.5, 98.5, 99.0) for m in range(5, 12)]
    orders = _run(_one_step_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 99.0, orders


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
