"""Расписание сторон внутри дня (tod_*): утром одна сторона, днём другая, вечером обе.

Заказ оператора 14.08.2026. Проверяем не «есть ли в коде ветка», а ФАКТ по позиции
робота на каждом баре: в окне, где сторона запрещена, позиция этой стороны не
появляется НИ РАЗУ, а уже открытая — закрывается.

Три вещи, каждая из которых уже стоила денег в соседних фильтрах:

1. ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ, БЕЗУСЛОВНО. Ключи вводятся в реестр, по которому
   посчитаны миллионы строк лидерборда; изменись поведение без tod_* — старые и
   новые строки стали бы несравнимы.
2. ЗАПРЕТ = ВЫХОД В ФЛЭТ, а не «сигнала нет». Ровно тем же способом устроен
   allow_long/allow_short: игноря сигнал, робот остался бы сидеть в запрещённой
   стороне без единого выхода.
3. ЧАСЫ БЕРУТСЯ ЧЕРЕЗ bar_offset_min. Бары бэктеста проштампованы МСК-стенным
   временем как UTC, а раннер агента строит истинно UTC-бары: без смещения окно
   «до 11:11» встало бы на 08:11 МСК (на этом уже горел us_open_fvg).
"""
import math

import pytest

from trader.lab.runtime import Bar, BacktestRuntime
from trader.lab.strategies.library import REGISTRY, _bar_minute, make_on_bar

SYM = "RIU6"
DAY0 = 1786579200          # 2026-08-13 00:00 UTC, ровно полночь: минута = индекс
M_1111 = 11 * 60 + 11      # 671
M_1900 = 19 * 60           # 1140

# Параметры сигнала берём короткие: тест должен прогреться за первые часы суток,
# иначе окна расписания не на чем проверять. Лестница и тейк выключены — здесь
# меряется гейт сторон, а не управление позицией.
BASE = {"symbol": SYM, "qty": 1, "fast": 3, "slow": 8, "signal": 3,
        "avg_max": 1, "avg_step_atr": 0, "tp_atr": 0, "sl_frac": 0, "sl_pct": 0,
        "avg_atr_n": 5, "nd_days": 1, "gap_auto": 0, "k_avg": 10,
        "min_gap_pts": 0, "cooldown_min": 0, "cooldown_pct": 1,
        "dv_bars": 0, "dv_range_pts": 0, "allow_long": 1, "allow_short": 1}


def _bars(n: int = 1439) -> list[Bar]:
    """Сутки минуток с разворотами каждые ~2 часа: обе стороны встречаются в КАЖДОМ
    окне расписания, иначе тест прошёл бы просто потому, что сигнала не было."""
    out, px = [], 90000.0
    for i in range(n):
        px += 900 * math.sin(2 * math.pi * i / 120) - 900 * math.sin(2 * math.pi * (i - 1) / 120)
        o = out[-1].close if out else px
        out.append(Bar(time=DAY0 + i * 60, open=o, high=max(o, px) + 5,
                       low=min(o, px) - 5, close=px, volume=100))
    return out


async def _run(params: dict) -> list[tuple[int, int]]:
    """Прогон суток. Возвращает [(минута МСК, позиция со знаком)] по каждому бару."""
    bars = _bars()
    rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=5_000_000.0)
    on_bar = make_on_bar("macd_shectory1")
    off = int(params.get("bar_offset_min", 0) or 0)
    seen: list[tuple[int, int]] = []
    while True:
        await on_bar(rt, params)
        pos = await rt.get_position(SYM)
        q = int(pos.quantity) * (1 if pos.side == "long" else -1 if pos.side == "short" else 0)
        seen.append((_bar_minute(bars[rt._cursor].time, off), q))
        if not rt.advance():
            return seen


@pytest.mark.asyncio
async def test_schedule_off_is_bit_for_bit_the_old_behaviour():
    """Без ключей и с выключенным расписанием — ровно один и тот же прогон."""
    plain = await _run(dict(BASE))
    zeroed = await _run(dict(BASE, tod_m1=0, tod_m2=0, tod_s1=1, tod_s2=2, tod_s3=3))
    assert plain == zeroed
    # Границы «наоборот» тоже не включают фильтр: расписание без порядка бессмысленно.
    reversed_bounds = await _run(dict(BASE, tod_m1=M_1900, tod_m2=M_1111, tod_s1=1))
    assert plain == reversed_bounds


@pytest.mark.asyncio
async def test_operator_schedule_holds_each_window():
    """Гипотеза оператора: до 11:11 только лонг, до 19:00 только шорт, вечером обе."""
    seen = await _run(dict(BASE, tod_m1=M_1111, tod_m2=M_1900,
                           tod_s1=1, tod_s2=2, tod_s3=3))
    assert any(q > 0 for m, q in seen if m < M_1111), "утром не случилось ни одного лонга"
    assert not any(q < 0 for m, q in seen if m < M_1111), "шорт в лонговом окне"
    assert any(q < 0 for m, q in seen if M_1111 <= m < M_1900), "днём не случилось шорта"
    assert not any(q > 0 for m, q in seen if M_1111 <= m < M_1900), "лонг в шортовом окне"
    evening = [q for m, q in seen if m >= M_1900]
    assert any(q > 0 for q in evening) and any(q < 0 for q in evening), \
        "вечером должны встречаться обе стороны"


@pytest.mark.asyncio
async def test_position_is_closed_when_its_side_becomes_forbidden():
    """Позиция, открытая в своём окне, обязана ЗАКРЫТЬСЯ на границе, а не зависнуть.

    Это и есть разница между «выход в флэт» и «игнорируем сигнал»: во втором
    случае лонг, открытый в 11:10, ехал бы через весь день в шортовом окне.
    """
    seen = await _run(dict(BASE, tod_m1=M_1111, tod_m2=M_1900,
                           tod_s1=1, tod_s2=2, tod_s3=3))
    after = [q for m, q in seen if M_1111 <= m < M_1111 + 60]
    assert after and max(after) <= 0, "лонг пережил границу лонгового окна"


@pytest.mark.asyncio
async def test_zero_mask_keeps_the_robot_out_of_the_market():
    """Маска 0 = вне рынка: в этом окне позиции нет вовсе, ни длинной, ни короткой."""
    seen = await _run(dict(BASE, tod_m1=M_1111, tod_m2=M_1900,
                           tod_s1=3, tod_s2=0, tod_s3=3))
    assert all(q == 0 for m, q in seen if M_1111 + 5 <= m < M_1900), \
        "робот торговал в окне, где стоять вне рынка"


@pytest.mark.asyncio
async def test_offset_moves_the_windows_by_three_hours():
    """bar_offset_min=180 сдвигает окна: тот же бар попадает в другое окно.

    Без этого деплой на агент (бары истинно UTC) отработал бы расписание на три
    часа раньше, чем задумано, и «утренний лонг» стал бы ночным.
    """
    a = await _run(dict(BASE, tod_m1=M_1111, tod_m2=M_1900, tod_s1=1, tod_s2=2, tod_s3=3))
    b = await _run(dict(BASE, tod_m1=M_1111, tod_m2=M_1900, tod_s1=1, tod_s2=2, tod_s3=3,
                        bar_offset_min=180))
    assert [q for _, q in a] != [q for _, q in b]


def test_boundary_minute_belongs_to_the_next_window():
    """Минута ровно на границе относится к СЛЕДУЮЩЕМУ окну — так написано в справке."""
    assert _bar_minute(DAY0 + M_1111 * 60) == M_1111
    assert _bar_minute(DAY0 + (M_1111 - 1) * 60) == M_1111 - 1
    assert _bar_minute(DAY0, 180) == 180          # смещение агента


def test_axis_is_exposed_by_every_registry_strategy():
    keys = {"tod_m1", "tod_m2", "tod_s1", "tod_s2", "tod_s3"}
    missing = [sid for sid, spec in REGISTRY.items()
               if not keys <= {p["key"] for p in spec["params_schema"]}]
    assert not missing, f"нет расписания сторон у: {', '.join(sorted(missing))}"


def test_defaults_keep_the_schedule_off():
    for sid, spec in REGISTRY.items():
        d = spec["default_params"]
        assert d.get("tod_m1") == 0 and d.get("tod_m2") == 0, sid
