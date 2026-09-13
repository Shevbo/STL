"""Valley Spike — лестница на медленной средней, какой она была до долины смерти.

Постановка оператора 13.09.2026. Каждый тест закрепляет ОДИН пункт по факту сделок.

Сценарий: день 1 — средняя свеча 20.0. День 2 — плато 100 (бары 1440-1459), спуск
96/90/84/78 (1460-1463) и долина на 78. Долина включается на баре 1472 (10 закрытий
подряд по 78, коридор 25% свечи = 5.0). SMA(20) до окна долины = 98.5, разрыв 20.5.
Лестница выше долины: 98.5 и 98.5 + D/2 = 103.625 (D = 50% разрыва = 10.25). Тейк на
полпути к середине 78: 88.25. Стоп за последней ступенью: 103.625 + 5.0 = 108.625.
"""
import asyncio

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.valley_spike import on_bar

SYM = "RIU6"
T0 = 1788307200                          # 00:00 UTC
DAY = 1440
TRIGGER = 1472

BASE = {"symbol": SYM, "qty": 1, "step_count": 2, "d_coef": 50, "vol_mult": 10,
        "max_contracts": 10, "dv_bars": 10, "dv_amp": 25, "amp_days": 5,
        "slow_n": 20, "min_gap_amp": 50, "mirror": 0, "ret_pct": 50,
        "stop_amp": 25, "slip_amp": 0, "max_hold": 60}


def _bars(closes: list[float], spikes: dict[int, float]) -> list[Bar]:
    """Размах бара 1.0; spikes[i] > 0 — верх фитиля, < 0 — низ (по модулю)."""
    out = []
    for i, c in enumerate(closes):
        hi, lo = c + 0.5, c - 0.5
        s = spikes.get(i)
        if s is not None:
            hi, lo = (max(hi, s), lo) if s > 0 else (hi, min(lo, -s))
        out.append(Bar(time=T0 + i * 60, open=c, high=hi, low=lo, close=c, volume=100))
    return out


def _series(tail: list[float], spikes: dict[int, float]) -> list[Bar]:
    day1 = [100.0] * DAY                                   # размах дня 90..110 = 20
    day2 = [100.0] * 20 + [96.0, 90.0, 84.0, 78.0] + tail  # хвост с бара 1464
    return _bars(day1 + day2, {100: 110.0, 101: -90.0, **spikes})


def _run(bars: list[Bar], **extra):
    async def go():
        rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
        p = {**BASE, **extra}
        while True:
            await on_bar(rt, p)
            if not rt.advance():
                break
        return [(o.side, int(o.qty), round(float(o.price), 4), (o.fill_time - T0) // 60)
                for o in rt._orders]
    return asyncio.run(go())


def test_fade_fills_at_frozen_pre_valley_average_and_takes_toward_valley():
    orders = _run(_series([78.0] * 20, {1475: 99.0}))
    assert orders[0] == ("sell", 1, 98.5, 1475), orders    # по средней ДО долины, не по 78
    assert orders[1] == ("buy", 1, 88.25, 1476), orders    # полпути к середине долины


def test_ladder_is_not_active_on_the_bar_that_turned_the_valley_on():
    # Долина включилась по закрытию бара 1472 — фитиль ЭТОГО бара заявку не налил:
    # её ещё не было в стакане.
    assert _run(_series([78.0] * 20, {TRIGGER: 99.0})) == []


def test_average_too_close_to_the_valley_is_skipped():
    # Разрыв 20.5 < 150% свечи (30.0): средняя не «дальняя», лестницы нет.
    assert _run(_series([78.0] * 20, {1475: 99.0}), min_gap_amp=150) == []


def test_ladder_is_cancelled_when_the_valley_ends_unfilled():
    # На баре 1476 закрытие 85 выводит из коридора — лестница снята, выброс на 1480
    # уже не наливает.
    assert _run(_series([78.0] * 12 + [85.0] * 10, {1480: 99.0})) == []


def test_ladder_survives_ttl_bars_after_the_valley_ends():
    # Та же долина кончается на баре 1476 (закрытие 85). С ttl_bars=10 лестница ещё
    # стоит, и выброс на 1480 наливает её по средней до долины; с ttl_bars=2 — уже нет.
    bars = _series([78.0] * 12 + [85.0] * 10, {1480: 99.0})
    assert _run(bars, ttl_bars=10)[0] == ("sell", 1, 98.5, 1480)
    assert _run(bars, ttl_bars=2) == []


def test_new_valley_cancels_a_ladder_still_living_on_ttl():
    # Долина на 78 кончилась на 1476, лестница живёт (ttl_bars=100). На 85 к бару 1485
    # складывается НОВАЯ долина с разрывом -0.8 — она не вооружается, но старый уровень
    # 98.5 обязана снять: внутри долины счётчик ttl не убывает, и без снятия прошлая
    # лестница простояла бы всю новую долину и налилась бы выбросом на 1487.
    bars = _series([78.0] * 12 + [85.0] * 30, {1487: 99.0})
    assert _run(bars, ttl_bars=100) == []


def test_stop_sits_beyond_the_last_step_and_gaps_fill_at_open():
    orders = _run(_series([78.0] * 11 + [78.0, 110.0] + [110.0] * 5, {1475: 99.0}))
    assert orders[0][:3] == ("sell", 1, 98.5), orders
    assert orders[1] == ("buy", 1, 110.0, 1476), orders    # открылся за стопом 108.625


def test_second_step_fills_as_limit_at_its_level():
    orders = _run(_series([78.0] * 11 + [78.0, 95.0] + [78.0] * 5, {1475: 99.0, 1476: 104.0}))
    assert orders[1] == ("sell", 1, 103.625, 1476), orders  # ступень 2 по уровню
    assert orders[2][:2] == ("buy", 2), orders             # закрыта вся позиция


def test_mirror_ladder_buys_the_spike_below_the_valley():
    bars = _series([78.0] * 20, {1475: -57.0})
    assert _run(bars) == []                                # без зеркала низа нет
    assert _run(bars, mirror=1)[0] == ("buy", 1, 57.5, 1475)   # 78 - 20.5
