"""Rich Fool — лестница ФЕЙДА импульса. Проверяем факт сделок робота на
синтетических днях, а не наличие веток в коде.

Логика оператора (09.09.2026, уточнена 12.09.2026):
  цена ускакала ВВЕРХ -> открываем ШОРТ и набираем позицию по уровням вверх;
  ускакала ВНИЗ      -> открываем ЛОНГ  и набираем по уровням вниз.
Уровни: price(0)=вчерашнее закрытие, price(n)=price(n-1)+amp/(n+1).
Стоп — стандартный, % ОТ ЦЕНЫ входа. Тейк — трейлинг от экстремума в нашу пользу,
срабатывает только в прибыли.

Что здесь закреплено (каждый пункт однажды ломался):
  1. Формула уровней: зазоры amp/2, amp/3, amp/4 — убывают.
  2. Направление: вверх = ШОРТ. invert=1 — обратный контроль.
  3. Все ступени, пересечённые ОДНИМ баром, исполняются в этом баре.
  4. Стоп = % от цены, филл по уровню; гэп сквозь стоп — по открытию.
  5. Трейлинг-тейк не выходит в убыток и не заглядывает внутрь бара.
  6. Позиция носится овернайт: в конце дня принудительно не закрывается.
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
    mid = (lo + hi) / 2
    return [
        _bar(day_epoch, OPEN_HM + 0, mid, hi, mid, hi),
        _bar(day_epoch, OPEN_HM + 1, hi, hi, lo, lo),
        _bar(day_epoch, OPEN_HM + 2, lo, mid, lo, last_close),
    ]


def _bars(day2_tail: list[Bar]) -> list[Bar]:
    # day -2: размах 90..110 (=20); day -1: 95..105 (=10), закрытие 100 -> amp=15.
    # Уровни ВВЕРХ (шорт):  107.5, 112.5, 116.25, 119.25, 121.75
    # Уровни ВНИЗ  (лонг):   92.5,  87.5,  83.75,  80.75,  78.25
    b = _prior_day(D0, 90.0, 110.0, 100.0) + _prior_day(D0 + DAY, 95.0, 105.0, 100.0)
    b.append(_bar(D0 + 2 * DAY, OPEN_HM - 1, 100.0, 100.1, 99.9, 100.0))  # арм-бар (вне окна)
    return b + day2_tail


# sl_price_pct=100 -> стоп 1.00% от цены; trail_tp_pct=50 -> трейлинг 0.50% от цены
PARAMS = {"symbol": SYM, "n_days": 2, "step_count": 2, "qty": 1,
          "sl_price_pct": 100, "trail_tp_pct": 50,
          "open_hour": 7, "open_min": 0, "place_lead_min": 10, "hold_min": 30}


async def _drive(day2_tail: list[Bar], extra: dict):
    d2 = D0 + 2 * DAY
    pad = [_bar(d2 + DAY, m, 107.0, 107.2, 106.8, 107.0) for m in range(0, 20)]
    rt = BacktestRuntime(bars=_bars(day2_tail) + pad, symbol=SYM, initial_equity=1_000_000.0)
    p = {**PARAMS, **extra}
    while True:
        await on_bar(rt, p)
        if not rt.advance():
            break
    return [(o.side, int(o.qty), round(float(o.price), 4)) for o in rt._orders]


def _run(day2_tail: list[Bar], **extra):
    return asyncio.run(_drive(day2_tail, extra))


def _entries(orders):
    """Филлы до первого разворота стороны = набор лестницы."""
    if not orders:
        return []
    side = orders[0][0]
    out = []
    for s, q, px in orders:
        if s != side:
            break
        out.append(px)
    return out


def _exit_price(orders):
    assert len(orders) >= 2, f"выхода не было: {orders}"
    return orders[-1][2]


# Стоп в % ОТ ЦЕНЫ по порядку величины ТУЖЕ, чем шаг лестницы (amp/3 на цене 100 это
# ~5%), поэтому в тестах на ГЕОМЕТРИЮ набора оба выхода глушатся заведомо огромными
# порогами — иначе стоп снимает позицию раньше второй ступени и мерить нечего.
# Само это взаимодействие — предмет перебора, а не дефект: см. ось sl_price_pct.
WIDE = {"sl_price_pct": 5000, "trail_tp_pct": 5000}


def _up_move() -> list[Bar]:
    """Цена уходит вверх, пересекая уровни шорт-лестницы 107.5 и 112.5."""
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 108.0, 100.0, 107.6))   # пересёк up[0]=107.5
    t.append(_bar(d2, OPEN_HM + 4, 107.6, 113.0, 107.6, 112.8))   # пересёк up[1]=112.5
    for i, m in enumerate(range(5, 28)):
        px = 113.0 + i * 0.2
        t.append(_bar(d2, OPEN_HM + m, px, px + 0.1, px - 0.1, px))
    return t


def test_up_move_opens_short_and_ladders_the_second_step():
    orders = _run(_up_move(), **WIDE)
    assert orders, "должна быть хотя бы одна сделка"
    assert orders[0][0] == "sell", f"цена ускакала вверх — робот обязан ШОРТИТЬ: {orders}"
    assert _entries(orders) == [107.5, 112.5], orders


def test_down_move_opens_long():
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 100.0, 92.0, 92.4))     # пересёк dn[0]=92.5
    t += [_bar(d2, OPEN_HM + m, 92.4, 92.6, 92.2, 92.4) for m in range(4, 28)]
    orders = _run(t, step_count=1)
    assert orders and orders[0][0] == "buy", f"цена ускакала вниз — робот обязан ЛОНГовать: {orders}"
    assert _entries(orders) == [92.5], orders


def test_harmonic_gaps_shrink_further_out():
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 2)]
    for i, m in enumerate(range(2, 120)):
        px = 100.0 + i * 0.25
        tail.append(_bar(d2, OPEN_HM + m, px, px + 0.1, px - 0.1, px + 0.1))
    px = _entries(_run(tail, step_count=5, hold_min=180, **WIDE))
    assert px == [107.5, 112.5, 116.25, 119.25, 121.75], px
    gaps = [round(px[i + 1] - px[i], 4) for i in range(4)]
    assert gaps == [5.0, 3.75, 3.0, 2.5], gaps
    assert all(gaps[i] > gaps[i + 1] for i in range(3)), f"зазоры строго убывают: {gaps}"


def test_all_steps_crossed_by_one_bar_fill_in_that_bar():
    """Бар прошил 3 уровня разом — 3 филла в ЭТОМ баре, а не по одному за минуту."""
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 117.0, 100.0, 116.8))   # up[0..2]=107.5/112.5/116.25
    t += [_bar(d2, OPEN_HM + m, 116.8, 117.0, 116.6, 116.8) for m in range(4, 28)]
    orders = _run(t, step_count=3, **WIDE)
    sells = [o for o in orders if o[0] == "sell"]
    assert len(sells) == 3, f"ожидали 3 ступени в одном баре: {orders}"
    assert _entries(orders) == [107.5, 112.5, 116.25], orders


def test_ladder_volume_grows_each_step():
    qtys = []
    for s, q, _ in _run(_up_move(), vol_mult=20, step_count=2, **WIDE):
        if s != "sell":
            break
        qtys.append(q)
    assert qtys == [1, 2], f"объёмы ступеней x2: ожидали [1,2], получили {qtys}"


def test_max_contracts_caps_the_ladder():
    signed = peak = 0
    for s, q, _ in _run(_up_move(), step_count=2, vol_mult=20, max_contracts=2, **WIDE):
        signed += q if s == "buy" else -q
        peak = max(peak, abs(signed))
    assert peak <= 2, f"позиция превысила потолок max_contracts=2: пик {peak}"


def test_inverted_turns_the_up_move_into_a_long():
    plain = _run(_up_move())
    inv = _run(_up_move(), invert=1)
    assert plain[0][0] == "sell" and inv[0][0] == "buy", (plain[0], inv[0])


def test_forbidden_side_blocks_entry():
    assert _run(_up_move(), allow_short=0) == [], "шорты запрещены — движение вверх не входит"


def test_no_move_no_trade():
    d2 = D0 + 2 * DAY
    calm = [_bar(d2, OPEN_HM + m, 100.0, 101.0, 99.0, 100.0) for m in range(0, 45)]
    assert _run(calm) == [], "цена не дошла до уровней — сделок нет"


def test_move_after_window_is_ignored():
    d2 = D0 + 2 * DAY
    late = [_bar(d2, OPEN_HM + m, 100.0, 100.5, 99.5, 100.0) for m in range(0, 31)]
    late.append(_bar(d2, OPEN_HM + 35, 100.0, 108.0, 100.0, 107.6))   # ПОСЛЕ окна (>30)
    late += [_bar(d2, OPEN_HM + m, 108.0, 109.0, 107.0, 108.0) for m in range(36, 45)]
    assert _run(late) == [], "движение за пределами hold_min не входит"


# ---------- стоп: процент ОТ ЦЕНЫ ----------

def _short_entry_then(tail: list[Bar]) -> list[Bar]:
    """Одна ступень шорта: вход 107.5 (avg=107.5)."""
    d2 = D0 + 2 * DAY
    t = [_bar(d2, OPEN_HM + m, 100.0, 100.2, 99.8, 100.0) for m in range(0, 3)]
    t.append(_bar(d2, OPEN_HM + 3, 100.0, 108.0, 100.0, 107.6))
    return t + tail


def test_stop_loss_is_a_percent_of_entry_price():
    """sl_price_pct=200 -> 2.00% от 107.5 = 2.15 -> стоп шорта на 109.65."""
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + m, 108.0, 108.4, 107.9, 108.2) for m in range(4, 8)]
    tail.append(_bar(d2, OPEN_HM + 8, 108.2, 110.0, 108.2, 109.9))   # прошил 109.65
    tail += [_bar(d2, OPEN_HM + m, 109.9, 110.1, 109.7, 109.9) for m in range(9, 28)]
    orders = _run(_short_entry_then(tail), step_count=1, sl_price_pct=200)
    assert _exit_price(orders) == 109.65, orders


def test_gap_through_the_stop_fills_at_the_open():
    """Бар открылся выше стопа (1% от 107.5 = 108.575) — выход по открытию 109.0."""
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + 4, 109.0, 109.5, 108.9, 109.2)]
    tail += [_bar(d2, OPEN_HM + m, 109.2, 109.4, 109.0, 109.2) for m in range(5, 28)]
    orders = _run(_short_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 109.0, orders


# ---------- трейлинг-тейк ----------

def test_trail_tp_exits_on_retrace_from_the_extreme():
    """Шорт 107.5, цена упала до 99.5 (экстремум), трейлинг 0.50% = 0.5375.
    Выход при откате вверх до 99.5+0.5375 = 100.0375."""
    d2 = D0 + 2 * DAY
    tail = []
    for i, m in enumerate(range(4, 11)):
        px = 106.0 - i * 1.0                       # 106..100, low = px-0.5 -> до 99.5
        tail.append(_bar(d2, OPEN_HM + m, px, px + 0.3, px - 0.5, px))
    tail.append(_bar(d2, OPEN_HM + 11, 100.0, 101.0, 100.0, 100.9))   # откат вверх
    tail += [_bar(d2, OPEN_HM + m, 100.9, 101.1, 100.7, 100.9) for m in range(12, 28)]
    orders = _run(_short_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 100.0375, orders


def test_trail_tp_never_exits_at_a_loss():
    """Цена сразу пошла ПРОТИВ шорта. Трейлинг от входа дал бы 108.0375 — это УБЫТОК,
    он обязан молчать; закрыть позицию имеет право только стоп (108.575)."""
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + m, 108.0, 108.4, 107.8, 108.2) for m in range(4, 9)]
    tail.append(_bar(d2, OPEN_HM + 9, 108.2, 109.0, 108.2, 108.9))    # прошил стоп 108.575
    tail += [_bar(d2, OPEN_HM + m, 108.9, 109.1, 108.7, 108.9) for m in range(10, 28)]
    orders = _run(_short_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 108.575, f"вышли не стопом, а трейлингом в убыток: {orders}"


def test_trail_tp_does_not_peek_inside_the_bar():
    """Один бар не может И задать экстремум своим low, И выбить тейк своим high.
    Бар 4: low 99.5 (новый экстремум), high 101.0 — если бы тейк считался по пику
    ЭТОГО бара (99.5+0.5375=100.0375), он сработал бы тут же. Должен — на следующем."""
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + 4, 100.5, 101.0, 99.5, 100.6)]
    tail += [_bar(d2, OPEN_HM + m, 100.6, 100.8, 100.4, 100.6) for m in range(5, 28)]
    orders = _run(_short_entry_then(tail), step_count=1)
    assert _exit_price(orders) == 100.0375, orders


def test_position_is_not_flattened_at_end_of_day():
    """Шорт в окне, дальше цена стоит между стопом и трейлингом до конца суток —
    позиция обязана дожить до следующего дня."""
    d2 = D0 + 2 * DAY
    tail = [_bar(d2, OPEN_HM + m, 107.5, 107.55, 107.45, 107.5) for m in range(4, 16 * 60)]
    orders = _run(_short_entry_then(tail), step_count=1)
    assert orders and orders[0][0] == "sell", orders
    assert [o for o in orders if o[0] == "buy"] == [], f"позицию закрыли до TP/SL: {orders}"
