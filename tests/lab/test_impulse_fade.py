"""Impulse Fade — заявки стоят далеко от цены и ждут «волосяного прокола».

Каждый тест закрепляет ОДИН пункт спецификации оператора от 12.09.2026 по ФАКТУ
сделок робота, а не по наличию веток в коде:

  1. Спокойный рынок заявку не задевает — сделок нет.
  2. Прокол исполняет заявку по цене УРОВНЯ, а не по close бара (первая версия
     стратегии входила рынком после импульса и брала середину отката).
  3. Сторона: прокол ВВЕРХ = ШОРТ (фейд). invert=1 — контроль.
  4. Тейк — возврат на ret_pct% импульса, а не фиксированный размер.
  5. Стоп — ЗА последней ступенью лестницы.
  6. Медленный ход якорь не обгоняет: дистанция не набирается, заявка цела.
  7. max_hold закрывает то, что не вернулось.
  8. Гейт боковика: тот же прокол в тренде сделки не даёт.

Числа индикаторов (EMA якоря, ATR) в тестах НЕ воспроизводятся — проверяются
структурные факты: сторона, цена филла относительно бара, порядок цен выхода.
"""
import asyncio

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.impulse_fade import on_bar

SYM = "RIU6"
T0 = 1788307200

# Короткий прогрев: якорь 10 баров, ATR 40. ATR длиннее нарочно — одиночный
# прокол не имеет права раздуть дистанцию до следующего уровня.
BASE = {"symbol": SYM, "qty": 1, "mean_n": 10, "atr_n": 40,
        "lvl_atr": 60, "step_count": 1, "step_atr": 20,
        "imp_bars": 3, "imp_frac": 50, "ret_pct": 50,
        "stop_atr": 20, "max_hold": 60, "cooldown": 5,
        "flat_only": 0, "reg_win": 200, "reg_drift": 300}


def _bars(closes: list[float], spikes: dict[int, float] | None = None) -> list[Bar]:
    """Ряд из закрытий: размах бара 1.0. spikes[i] = верх (>0) или низ (<0) прокола."""
    spikes = spikes or {}
    out = []
    for i, c in enumerate(closes):
        hi, lo = c + 0.5, c - 0.5
        s = spikes.get(i)
        if s is not None:
            hi, lo = (max(hi, s), lo) if s > 0 else (hi, min(lo, -s))
        out.append(Bar(time=T0 + i * 60, open=c, high=hi, low=lo, close=c, volume=100))
    return out


def _run(bars: list[Bar], **extra):
    async def go():
        rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
        p = {**BASE, **extra}
        while True:
            await on_bar(rt, p)
            if not rt.advance():
                break
        return [(o.side, int(o.qty), round(float(o.price), 4)) for o in rt._orders]
    return asyncio.run(go())


QUIET = [100.0] * 60                      # прогрев: якорь ~100, ATR ~1.0


def test_quiet_market_never_touches_the_order():
    assert _run(_bars(QUIET + [100.0] * 40)) == []


def test_spike_fills_at_level_not_at_close():
    # Прокол вверх до 107 одним баром; close бара остаётся 100.
    bars = _bars(QUIET + [100.0] * 5, spikes={62: 107.0})
    orders = _run(bars)
    assert orders, "прокол обязан исполнить стоящую заявку"
    side, qty, px = orders[0]
    assert side == "sell" and qty == 1          # фейд хода вверх
    # Заявка стояла ДАЛЕКО: филл около 6 ATR над якорем, а не по close=100.
    assert 105.0 < px < 107.0, px


def test_invert_mirrors_the_side():
    bars = _bars(QUIET + [100.0] * 5, spikes={62: 107.0})
    assert _run(bars, invert=1)[0][0] == "buy"


def test_down_spike_is_faded_with_a_long():
    bars = _bars(QUIET + [100.0] * 5, spikes={62: -93.0})
    side, _, px = _run(bars)[0]
    assert side == "buy" and 93.0 < px < 95.0, (side, px)


def test_take_is_a_fraction_of_the_impulse():
    # После прокола цена возвращается к 100. При ret_pct=100 выход обязан быть
    # ДАЛЬШЕ (ниже для шорта), чем при 50: это доля одного и того же импульса.
    bars = _bars(QUIET + [100.0] + [99.0] * 30, spikes={60: 107.0})
    half = _run(bars, ret_pct=50)
    full = _run(bars, ret_pct=100)
    assert len(half) >= 2 and len(full) >= 2, (half, full)
    assert half[1][0] == full[1][0] == "buy"
    assert full[1][2] < half[1][2] < half[0][2], (half, full)   # оба в прибыли


def test_stop_sits_beyond_the_ladder():
    # Прокол не вернулся — ушёл выше. Выход обязан быть ХУЖЕ входа и выше уровня.
    bars = _bars(QUIET + [100.0] + [104.0, 108.0, 112.0, 116.0, 120.0] * 4,
                 spikes={60: 107.0})
    orders = _run(bars)
    assert len(orders) >= 2
    entry, exit_ = orders[0], orders[1]
    assert exit_[0] == "buy" and exit_[2] > entry[2], orders    # убыток по шорту


def test_slow_drift_never_reaches_the_level():
    # Ровный ход +0.2 за бар: якорь EMA(10) тянется следом, дистанция 6 ATR не
    # набирается ни разу. Именно это отличает прокол от тренда.
    bars = _bars(QUIET + [100.0 + 0.2 * i for i in range(1, 120)])
    assert _run(bars) == []


def test_max_hold_closes_what_did_not_return():
    # Цена замерла между тейком и стопом: закрыть обязано время.
    bars = _bars(QUIET + [100.0] + [104.5] * 40, spikes={60: 107.0})
    orders = _run(bars, max_hold=10, ret_pct=50, stop_atr=100)
    assert len(orders) == 2 and orders[1][0] == "buy", orders
    assert abs(orders[1][2] - 104.5) < 0.01, orders             # выход по close


def test_flat_gate_blocks_the_same_spike_in_a_trend():
    rise = [100.0 * (1.0012 ** i) for i in range(260)]          # +36% за окно
    bars = _bars(rise + [rise[-1]] * 6, spikes={261: rise[-1] * 1.07})
    assert _run(bars, flat_only=1) == [], "прокол в тренде — не прокол"
    assert _run(bars, flat_only=0), "без гейта тот же прокол торгуется"


def test_flat_gate_allows_a_sideways_market():
    calm = [100.0 + (0.4 if i % 2 else -0.4) for i in range(260)]
    bars = _bars(calm + [100.0] * 6, spikes={261: 110.0})   # зигзаг держит ATR выше, чем ровный ряд
    assert _run(bars, flat_only=1), "в боковике гейт обязан пропускать"


def test_distance_in_daily_candles_replaces_atr():
    # lvl_amp>0 ОТМЕНЯЕТ lvl_atr: два дня размахом 10.0 дают среднюю свечу 10.0,
    # и при lvl_amp=50 заявка встаёт в 5.0 от якоря — прокол до 107 её берёт,
    # а тот же прокол при lvl_amp=150 (дистанция 15.0) не достаёт.
    day = 1440
    d1 = [100.0 + (5.0 if i == 10 else (-5.0 if i == 20 else 0.0)) for i in range(day)]
    d2 = list(d1)
    tail = [100.0] * 30
    bars = _bars(d1 + d2 + tail, spikes={2 * day + 5: 107.0})
    assert _run(bars, lvl_amp=50, amp_days=5, imp_frac=0)[0][0] == "sell"
    assert _run(bars, lvl_amp=150, amp_days=5, imp_frac=0) == []


def test_first_day_has_no_daily_range_yet():
    # Завершённых дней нет — дистанции нет, торговать нельзя (не по ATR молча).
    bars = _bars([100.0] * 300, spikes={200: 130.0})
    assert _run(bars, lvl_amp=50) == []
