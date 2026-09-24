"""Финальная доливка на убыточном кроссовере: режим 2 идёт СВЕРХ потолка.

Заказ оператора 24.09.2026. Повод: замер того же дня показал, что режим 1 почти
ничего не менял — к моменту убыточного кроссовера позиция уже упирается в avg_max,
остаток до потолка ноль, и доливать нечем ровно в тех эпизодах, ради которых ось
заводилась (при живом расписании режим 1 был хуже базы 8/8 на RIH6, 6/8 на RIM6,
8/8 на RIU6).

Тест зеркалит арифметику из make_on_bar, как соседние тесты движка.
"""
from trader.lab.strategies.library import REGISTRY


def final_add(step: int, k_avg: float, cur: int, avg_max: int, mode: int,
              step_exec: int | None = None) -> int:
    """Сколько контрактов долить на убыточном развороте (0 = не доливаем).

    step — НОМИНАЛЬНАЯ следующая ступень (её хранит avg_add), step_exec —
    фактически исполненная (avg_add_exec). На полной позиции они расходятся:
    при лестнице 1-2-4-8 и потолке 20 исполнено 5, номинал 16.
    """
    if not mode:
        return 0
    room = avg_max - abs(cur) if mode == 1 else 10 ** 9
    if room <= 0:
        return 0
    base = step_exec if (mode == 3 and step_exec is not None) else step
    return max(0, min(int(base * k_avg + 0.5), room))


LIVE_K = 2.0          # k_avg=20 в живой спеке хранится x10


def test_mode1_cannot_add_at_the_ceiling():
    """Позиция на потолке — режим 1 бессилен, это и была причина нулевого эффекта."""
    assert final_add(8, LIVE_K, cur=20, avg_max=20, mode=1) == 0


def test_mode2_adds_the_next_step_over_the_ceiling():
    """Следующая ступень = предыдущая x k_avg: при 8 и k=2.0 доливается 16."""
    assert final_add(8, LIVE_K, cur=20, avg_max=20, mode=2) == 16


def test_mode1_still_capped_by_the_room_below_the_ceiling():
    assert final_add(8, LIVE_K, cur=14, avg_max=20, mode=1) == 6
    assert final_add(8, LIVE_K, cur=14, avg_max=20, mode=2) == 16


def test_off_means_off():
    assert final_add(8, LIVE_K, cur=20, avg_max=20, mode=0) == 0


def test_schema_allows_the_new_value():
    spec = next(s for s in REGISTRY["macd_shectory1"]["params_schema"]
                if s["key"] == "flip_add_max")
    assert spec["max"] >= 2, spec


def test_mode3_doubles_the_EXECUTED_step_not_the_nominal_one():
    """Уточнение оператора 24.09: «х2 от предыдущей» = от исполненной ступени.

    Позиция 20 на потолке: номинальная следующая ступень 16 (её хранит avg_add),
    фактически исполненная 5. Режим 2 доливает 32 (позиция 52), режим 3 — 10
    (позиция 30).
    """
    assert final_add(16, LIVE_K, cur=20, avg_max=20, mode=2, step_exec=5) == 32
    assert final_add(16, LIVE_K, cur=20, avg_max=20, mode=3, step_exec=5) == 10


def test_mode3_still_ignores_the_ceiling():
    assert final_add(4, LIVE_K, cur=20, avg_max=20, mode=3, step_exec=4) == 8


def test_schema_allows_mode3():
    spec = next(s for s in REGISTRY["macd_shectory1"]["params_schema"]
                if s["key"] == "flip_add_max")
    assert spec["max"] >= 3, spec


def test_state_reset_is_not_testable_by_mirroring():
    """Зеркало арифметики НЕ ловит несброс состояния — ловит только прогон.

    24.09 проверка нашла в режиме 3 баг, которого этот файл поймать не мог:
    ключ avg_add_exec не сбрасывался на новой позиции, и финальная доливка на
    свежей позиции из одного лота брала ступень ПРЕДЫДУЩЕЙ позиции (26 таких
    доливок по 4-20 лотов, пик до 21 вместо 2). Проверка ниже гоняет движок.
    """
    import asyncio

    from trader.lab.backtest import run_single_backtest
    from trader.lab.runtime import Bar
    from trader.lab.strategies import library

    def bars(n=600):
        out, px, t = [], 100000.0, 1_700_000_000
        for i in range(n):
            px += (150.0 if (i // 30) % 2 == 0 else -150.0)
            out.append(Bar(time=t + i * 60, open=px, high=px + 50, low=px - 50,
                           close=px, volume=100))
        return out

    mod = type("M", (), {"on_bar": library.make_on_bar("macd_shectory1")})
    p = {"symbol": "TEST", "qty": 1, "fast": 5, "slow": 12, "signal": 4,
         "avg_atr_n": 14, "avg_step_atr": 21, "avg_max": 4, "k_avg": 20,
         "tp_atr": 80, "sl_pct": 100, "flip_add_max": 3, "flatten_end": 1}
    res = asyncio.run(run_single_backtest(mod, bars(), "TEST", p, point_value=1.0))
    peak, signed = 0, 0
    for t in res["trades"]:
        signed += t["qty"] * (1 if t["side"] == "buy" else -1)
        peak = max(peak, abs(signed))
    # Потолок лестницы 4, доливка сверх него = 2x фактической ступени. Даже в
    # худшем случае это 4 + 2*4 = 12; при несброшенном состоянии пик уезжал выше.
    assert peak <= 12, f"пик позиции {peak} — похоже, состояние не сбрасывается"
