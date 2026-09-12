"""Rich Fool — предоткрытийная лестница фейда импульса ОТКРЫТИЯ.

Каждый тест закрепляет ОДИН пункт спецификации оператора от 12.09.2026, по факту
сделок робота, а не по наличию веток в коде.

  1. d_coef сужает лестницу: D = amp × d_coef.
  2. Объём — ОДИН дробный множитель к предыдущей заявке.
  3. Направление: цена ВВЕРХ = ШОРТ (фейд). invert=1 — контроль.
  4. Стоп от средней и ЗА пределами всех заявок лестницы.
  5. Заявки снимаются через hold_min ТОЛЬКО если не было ни одной сделки.
  6. Овернайт ЗАПРЕЩЁН: на закрытии сессии позиция закрывается принудительно.
  7. За exit_lead_min до закрытия — выход по сигналу двух EMA.
  8. Границы сессии берутся ИЗ ИСТОРИИ (расписание FORTS менялось внутри периода:
     у RIM6 в марте-июне НИ ОДНОГО дня с баром 07:00, будни открывались в 09:00).
"""
import asyncio

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.rich_fool import on_bar

SYM = "RIU6"
D0 = 1788307200                       # СРЕДА 00:00 UTC (= МСК-стенка)
DAY = 86400
WD_OPEN, WD_CLOSE = 7 * 60, 23 * 60 + 50      # будни  07:00 .. 23:50
WE_OPEN, WE_CLOSE = 10 * 60, 19 * 60          # выходные 10:00 .. 19:00


def _bar(day_epoch: int, minute: int, o, h, low, c) -> Bar:
    return Bar(time=day_epoch + minute * 60, open=o, high=h, low=low, close=c, volume=100)


def _prior_day(day_epoch: int, lo: float, hi: float, last_close: float,
               open_m: int = WD_OPEN, close_m: int = WD_CLOSE) -> list[Bar]:
    """Прошлый день: размах [lo, hi], закрытие last_close. Последний бар стоит в
    close_m — именно из него стратегия выводит ожидаемое закрытие сессии."""
    mid = (lo + hi) / 2
    return [
        _bar(day_epoch, open_m + 0, mid, hi, mid, hi),
        _bar(day_epoch, open_m + 1, hi, hi, lo, lo),
        _bar(day_epoch, open_m + 2, lo, mid, lo, last_close),
        _bar(day_epoch, close_m, last_close, last_close, last_close, last_close),
    ]


# day -2: размах 90..110 (=20); day -1: 95..105 (=10), закрытие 100 -> amp = 15.
# d_coef=100 -> D=15. Уровни ВВЕРХ (шорт): 107.5, 112.5, 116.25
#                      уровни ВНИЗ (лонг):  92.5,  87.5,  83.75
PRIOR = _prior_day(D0, 90.0, 110.0, 100.0) + _prior_day(D0 + DAY, 95.0, 105.0, 100.0)

BASE = {"symbol": SYM, "n_days": 2, "qty": 1, "step_count": 3, "d_coef": 100,
        "vol_mult": 10, "max_contracts": 100,
        "sl_price_pct": 100, "trail_tp_pct": 50,
        "place_lead_min": 10, "hold_min": 30,
        "ema_fast": 9, "ema_slow": 21, "exit_lead_min": 0,
        # Цены в фикстурах около 100, поэтому защита в пунктах здесь глушится:
        # иначе она запретит любой вход и геометрию мерить будет нечем.
        "slip_guard_pts": 0, "slip_pct": 0}

# Глушим выходы, когда мерим ГЕОМЕТРИЮ набора: иначе стоп/тейк снимут позицию
# раньше, чем лестница добрала, и мерить нечего.
WIDE = {"sl_price_pct": 5000, "trail_tp_pct": 5000, "exit_lead_min": 0}


async def _drive(prior: list[Bar], tail: list[Bar], extra: dict):
    rt = BacktestRuntime(bars=prior + tail, symbol=SYM, initial_equity=1_000_000.0)
    p = {**BASE, **extra}
    while True:
        await on_bar(rt, p)
        if not rt.advance():
            break
    out = []
    for o in rt._orders:
        t = (o.fill_time or 0) % DAY
        out.append((o.side, int(o.qty), round(float(o.price), 4), t // 60))
    return out


def _run(tail: list[Bar], prior: list[Bar] | None = None, **extra):
    return asyncio.run(_drive(PRIOR if prior is None else prior, tail, extra))


def _entries(orders):
    if not orders:
        return []
    side, out = orders[0][0], []
    for s, q, px, _ in orders:
        if s != side:
            break
        out.append(px)
    return out


def _exits(orders):
    if not orders:
        return []
    side = orders[0][0]
    return [(q, px, hm) for s, q, px, hm in orders if s != side]


def _day(n: int) -> int:
    return D0 + n * DAY


def _tail(bars_spec, open_hm=WD_OPEN, day_n=2) -> list[Bar]:
    """Первый бар дня (он же бар вооружения) + бары дня, минуты от открытия."""
    d = _day(day_n)
    out = [_bar(d, open_hm - 5, 100.0, 100.1, 99.9, 100.0)]
    for off, o, h, low, c in bars_spec:
        out.append(_bar(d, open_hm + off, o, h, low, c))
    return out


# ── 1. d_coef сужает лестницу ─────────────────────────────────────────────────

def test_d_coef_narrows_the_ladder():
    """D = amp × d_coef. amp=15: при 1.0 первая ступень 107.5, при 0.2 — 101.5."""
    spec = [(m, 100.0 + m * 0.5, 100.0 + m * 0.5 + 0.05, 100.0 + m * 0.5 - 0.05,
             100.0 + m * 0.5) for m in range(0, 26)]
    assert _entries(_run(_tail(spec), step_count=1, **WIDE)) == [107.5]
    assert _entries(_run(_tail(spec), step_count=1, d_coef=20, **WIDE)) == [101.5]


def test_harmonic_gaps_shrink_further_out():
    spec = [(m, 100.0 + m * 0.25, 100.0 + m * 0.25 + 0.05, 100.0 + m * 0.25 - 0.05,
             100.0 + m * 0.25) for m in range(0, 120)]
    px = _entries(_run(_tail(spec), step_count=3, hold_min=180, **WIDE))
    assert px == [107.5, 112.5, 116.25], px
    assert [round(px[i + 1] - px[i], 4) for i in range(2)] == [5.0, 3.75]


# ── 2. объём: один дробный множитель к предыдущей заявке ──────────────────────

def _slam_up():
    """Один бар прошивает все три ступени шорта, дальше тишина."""
    return [(0, 100.0, 117.0, 100.0, 116.8)] + [(m, 116.8, 116.9, 116.7, 116.8)
                                                for m in range(1, 25)]


def test_volume_is_one_fractional_multiplier_of_the_previous_order():
    q2 = [q for s, q, _, _ in _run(_tail(_slam_up()), step_count=3, vol_mult=20, **WIDE)
          if s == "sell"]
    assert q2 == [1, 2, 4], f"×2.0 от предыдущей: ждали [1,2,4], получили {q2}"
    q15 = [q for s, q, _, _ in _run(_tail(_slam_up()), step_count=3, vol_mult=15, **WIDE)
           if s == "sell"]
    assert q15 == [1, 2, 2], f"×1.5 от предыдущей (с округлением): {q15}"


def test_max_contracts_caps_the_ladder():
    signed = peak = 0
    for s, q, _, _ in _run(_tail(_slam_up()), step_count=3, vol_mult=20,
                           max_contracts=2, **WIDE):
        signed += q if s == "buy" else -q
        peak = max(peak, abs(signed))
    assert peak <= 2, f"позиция превысила потолок: пик {peak}"


# ── 3. направление ────────────────────────────────────────────────────────────

def test_up_move_is_a_short_and_down_move_is_a_long():
    up = [(0, 100.0, 108.0, 100.0, 107.6)] + [(m, 107.6, 107.7, 107.5, 107.6)
                                              for m in range(1, 25)]
    dn = [(0, 100.0, 100.0, 92.0, 92.4)] + [(m, 92.4, 92.5, 92.3, 92.4)
                                            for m in range(1, 25)]
    o_up = _run(_tail(up), step_count=1, **WIDE)
    o_dn = _run(_tail(dn), step_count=1, **WIDE)
    assert o_up and o_up[0][0] == "sell", f"вверх обязан быть ШОРТ: {o_up}"
    assert _entries(o_up) == [107.5], o_up
    assert o_dn and o_dn[0][0] == "buy", f"вниз обязан быть ЛОНГ: {o_dn}"
    assert _entries(o_dn) == [92.5], o_dn


def test_invert_is_the_control_run():
    up = [(0, 100.0, 108.0, 100.0, 107.6)] + [(m, 107.6, 107.7, 107.5, 107.6)
                                              for m in range(1, 25)]
    plain = _run(_tail(up), step_count=1, **WIDE)
    inv = _run(_tail(up), step_count=1, invert=1, **WIDE)
    assert plain[0][0] == "sell" and inv[0][0] == "buy", (plain[0], inv[0])


# ── 4. стоп за пределами лестницы ─────────────────────────────────────────────

def test_stop_sits_beyond_the_last_ladder_step():
    """Уровни шорта 107.5/112.5/116.25. Стоп = последняя ступень 116.25 + 1% от
    средней, то есть ЗА лестницей. Бар с high 117.0 добирает ступени и НЕ выбивает
    стоп; бар с high 118.0 — выбивает."""
    inside = [(0, 100.0, 108.0, 100.0, 107.6), (1, 107.6, 117.0, 107.6, 116.9)]
    inside += [(m, 116.9, 117.0, 116.8, 116.9) for m in range(2, 20)]
    o = _run(_tail(inside), step_count=3, trail_tp_pct=5000, exit_lead_min=0)
    assert [s for s, _, _, _ in o].count("sell") == 3, f"ждали три ступени шорта: {o}"
    assert [s for s, _, _, _ in o].count("buy") == 0, f"стоп сработал ВНУТРИ лестницы: {o}"

    beyond = [(0, 100.0, 108.0, 100.0, 107.6), (1, 107.6, 118.0, 107.6, 117.9)]
    beyond += [(m, 117.9, 118.0, 117.8, 117.9) for m in range(2, 20)]
    o2 = _run(_tail(beyond), step_count=3, trail_tp_pct=5000, exit_lead_min=0)
    assert any(s == "buy" for s, _, _, _ in o2), f"стоп за лестницей не сработал: {o2}"


# ── 5. снятие заявок только при отсутствии сделок ─────────────────────────────

def test_orders_are_cancelled_after_window_only_when_nothing_filled():
    late = [(m, 100.0, 100.5, 99.5, 100.0) for m in range(0, 40)]
    late += [(45, 100.0, 108.0, 100.0, 107.6)]
    late += [(m, 107.6, 107.7, 107.5, 107.6) for m in range(46, 70)]
    assert _run(_tail(late), step_count=1, **WIDE) == [], "окно истекло — входа нет"


def test_ladder_keeps_working_past_the_window_once_it_has_filled():
    """Первая ступень сработала В окне, вторая — ПОСЛЕ него. Лестница обязана
    добрать: снимаются только заявки, по которым не было ни одной сделки."""
    spec = [(5, 100.0, 108.0, 100.0, 107.6)]
    spec += [(m, 107.6, 107.7, 107.5, 107.6) for m in range(6, 40)]
    spec += [(40, 107.6, 113.0, 107.6, 112.8)]
    spec += [(m, 112.8, 112.9, 112.7, 112.8) for m in range(41, 60)]
    px = _entries(_run(_tail(spec), step_count=2, **WIDE))
    assert px == [107.5, 112.5], f"вторая ступень после окна не добрала: {px}"


# ── 6. овернайт запрещён ──────────────────────────────────────────────────────

def test_no_overnight_position_is_closed_at_session_close():
    spec = [(0, 100.0, 108.0, 100.0, 107.6)]
    spec += [(m, 107.45, 107.55, 107.40, 107.5) for m in range(1, WD_CLOSE - WD_OPEN + 5)]
    o = _run(_tail(spec), step_count=1, trail_tp_pct=5000, exit_lead_min=0)
    assert o and o[0][0] == "sell", o
    ex = _exits(o)
    assert ex, f"позиция осталась на ночь: {o}"
    assert ex[0][2] >= WD_CLOSE, f"вышли раньше закрытия: {ex[0]}"


# ── 7. выход по двум EMA перед закрытием ──────────────────────────────────────

def test_two_ema_exit_fires_before_the_close():
    """Шорт, затем цена устойчиво РАСТЁТ: быстрая EMA выше медленной — сигнал
    против шорта. В окне exit_lead_min робот выходит, не дожидаясь закрытия."""
    spec = [(0, 100.0, 108.0, 100.0, 107.6)]
    spec += [(m, 107.0, 107.1, 106.9, 107.0) for m in range(1, 700)]
    spec += [(m, 107.0 + (m - 700) * 0.02, 107.0 + (m - 700) * 0.02 + 0.05,
              107.0 + (m - 700) * 0.02 - 0.05, 107.0 + (m - 700) * 0.02)
             for m in range(700, WD_CLOSE - WD_OPEN + 5)]
    o = _run(_tail(spec), step_count=1, sl_price_pct=2000,
             trail_tp_pct=5000, exit_lead_min=120)
    assert o and o[0][0] == "sell", o
    ex = _exits(o)
    assert ex, f"выхода не было вовсе: {o}"
    assert ex[0][2] < WD_CLOSE, f"вышли только на закрытии, а не по EMA: {ex[0]}"
    assert ex[0][2] >= WD_CLOSE - 120, f"EMA-выход сработал вне своего окна: {ex[0]}"


# ── 8. границы сессии из истории ──────────────────────────────────────────────

def test_weekend_session_falls_back_to_nineteen_when_no_weekend_history():
    """Суббота, в истории только будни: закрытие неизвестно — берём запасное 19:00."""
    sat = 3                       # D0 = среда -> +3 = суббота
    spec = [(0, 100.0, 108.0, 100.0, 107.6)]
    spec += [(m, 107.45, 107.55, 107.40, 107.5) for m in range(1, WE_CLOSE - WE_OPEN + 5)]
    o = _run(_tail(spec, open_hm=WE_OPEN, day_n=sat), step_count=1,
             trail_tp_pct=5000, exit_lead_min=0)
    assert o and o[0][0] == "sell", f"в выходной лестница не сработала: {o}"
    assert _entries(o) == [107.5], o
    ex = _exits(o)
    assert ex, f"в выходной позиция осталась открытой: {o}"
    assert WE_CLOSE <= ex[0][2] < WD_CLOSE, \
        f"закрылись не по расписанию выходного (ждали ~{WE_CLOSE}): {ex[0]}"


def test_session_close_is_taken_from_the_previous_day_of_the_same_kind():
    """Воскресенье. В истории есть СУББОТА, у которой последний бар в 18:30 —
    закрытие обязано прийти из неё, а не из запасного 19:00 и не из будней."""
    sat_close = 18 * 60 + 30
    prior = (_prior_day(D0, 90.0, 110.0, 100.0)                      # ср, размах 20
             + _prior_day(D0 + DAY, 95.0, 105.0, 100.0)              # чт, размах 10
             + _prior_day(D0 + 2 * DAY, 98.0, 102.0, 100.0)          # пт, размах 4
             + _prior_day(D0 + 3 * DAY, 98.0, 102.0, 100.0,          # СБ, размах 4
                          open_m=WE_OPEN, close_m=sat_close))
    # Суббота вооружается на amp(чт,пт)=7 -> D=7, уровень 103.5 — её размах 98..102
    # до него не доходит, поэтому в субботу сделки нет и вход случится в воскресенье.
    # Воскресенье: amp(пт,сб)=4 -> D=4 -> уровень шорта 102.0
    spec = [(0, 100.0, 103.0, 100.0, 102.8)]
    spec += [(m, 102.0, 102.1, 101.9, 102.0) for m in range(1, WE_CLOSE - WE_OPEN + 5)]
    o = _run(_tail(spec, open_hm=WE_OPEN, day_n=4), prior=prior,
             step_count=1, trail_tp_pct=5000, exit_lead_min=0)
    assert o and o[0][0] == "sell", f"лестница не сработала: {o}"
    assert _entries(o) == [102.0], o
    ex = _exits(o)
    assert ex, f"позиция не закрыта: {o}"
    assert sat_close <= ex[0][2] < WE_CLOSE, \
        f"закрытие взято не из субботы ({sat_close}): {ex[0]}"


# ── 9. защита от проскальзывания (лимитный вход) ──────────────────────────────

def _touch_then_through():
    """Сначала бар КАСАЕТСЯ уровня 107.5 и отскакивает, потом проходит сквозь."""
    spec = [(0, 100.0, 107.5, 100.0, 104.0)]                  # ровно касание
    spec += [(m, 104.0, 104.2, 103.8, 104.0) for m in range(1, 6)]
    spec += [(6, 104.0, 110.0, 104.0, 109.5)]                 # проход сквозь
    spec += [(m, 109.5, 109.7, 109.3, 109.5) for m in range(7, 30)]
    return spec


def test_slip_guard_rejects_a_touch_and_accepts_a_pass_through():
    """Лимитник в очереди: касание уровня с отскоком наливает тех, кто впереди.
    С защитой 2 пункта вход должен случиться не на касании 107.5, а на проходе."""
    free = _run(_tail(_touch_then_through()), step_count=1, slip_guard_pts=0, **WIDE)
    guarded = _run(_tail(_touch_then_through()), step_count=1, slip_guard_pts=2, **WIDE)
    assert free and free[0][3] == WD_OPEN, f"без защиты вход на касании: {free[:1]}"
    assert guarded, f"с защитой входа не случилось вовсе: {guarded}"
    assert guarded[0][3] == WD_OPEN + 6, f"с защитой вход обязан быть на проходе: {guarded[:1]}"


def test_slip_guard_does_not_apply_to_the_breakout_side():
    """Вход по пробою — СТОПОВАЯ заявка, она срабатывает по касанию уровня.
    Защита от проскальзывания к ней не относится (у стопа есть слип, а не очередь)."""
    o = _run(_tail(_touch_then_through()), step_count=1, invert=1,
             slip_guard_pts=2, **WIDE)
    assert o and o[0][0] == "buy", f"пробой вверх обязан покупать: {o[:1]}"
    assert o[0][3] == WD_OPEN, f"стоп обязан сработать на касании: {o[:1]}"


# ── 10. проскальзывание стоповых исполнений ──────────────────────────────────

def test_breakout_entry_pays_slippage_but_fade_entry_does_not():
    up = [(0, 100.0, 108.0, 100.0, 107.6)] + [(m, 107.6, 107.7, 107.5, 107.6)
                                              for m in range(1, 25)]
    fade0 = _run(_tail(up), step_count=1, slip_pct=0, **WIDE)
    fade1 = _run(_tail(up), step_count=1, slip_pct=100, **WIDE)
    assert fade0[0][2] == fade1[0][2],         f"лимитный вход фейда проскальзывания не имеет: {fade0[0]} против {fade1[0]}"
    brk0 = _run(_tail(up), step_count=1, invert=1, slip_pct=0, **WIDE)
    brk1 = _run(_tail(up), step_count=1, invert=1, slip_pct=100, **WIDE)
    assert brk1[0][2] > brk0[0][2],         f"стоповая покупка по пробою обязана налиться ДОРОЖЕ: {brk0[0]} против {brk1[0]}"


def test_stop_loss_and_trailing_take_both_pay_slippage():
    """Стоп-лосс и трейлинг-тейк — оба СТОПЫ по механике, оба наливаются по рынку."""
    # стоп-лосс: шорт, цена уходит против за последнюю ступень
    beyond = [(0, 100.0, 108.0, 100.0, 107.6), (1, 107.6, 118.0, 107.6, 117.9)]
    beyond += [(m, 117.9, 118.0, 117.8, 117.9) for m in range(2, 20)]
    sl0 = _exits(_run(_tail(beyond), step_count=3, trail_tp_pct=5000, slip_pct=0))
    sl1 = _exits(_run(_tail(beyond), step_count=3, trail_tp_pct=5000, slip_pct=100))
    assert sl0 and sl1, (sl0, sl1)
    assert sl1[0][1] > sl0[0][1],         f"стоп шорта обязан откупиться ДОРОЖЕ: {sl0[0]} против {sl1[0]}"

    # трейлинг-тейк: шорт, цена падает, затем откат
    fall = [(0, 100.0, 108.0, 100.0, 107.6)]
    for i, m in enumerate(range(1, 8)):
        px = 106.0 - i * 1.0
        fall.append((m, px, px + 0.3, px - 0.5, px))
    fall += [(8, 100.0, 101.0, 100.0, 100.9)]
    fall += [(m, 100.9, 101.1, 100.7, 100.9) for m in range(9, 28)]
    tp0 = _exits(_run(_tail(fall), step_count=1, slip_pct=0))
    tp1 = _exits(_run(_tail(fall), step_count=1, slip_pct=100))
    assert tp0 and tp1, (tp0, tp1)
    assert tp1[0][1] > tp0[0][1],         f"трейлинг шорта обязан откупиться ДОРОЖЕ: {tp0[0]} против {tp1[0]}"
