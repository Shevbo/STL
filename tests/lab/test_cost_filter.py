"""Фильтр издержек: не входить, когда ход бара меньше цены круга.

Что легко сломать и что проверяем:
  1. выключенный (cost_atr=0) не меняет НИ ОДНОЙ сделки — иначе это не фильтр, а правка;
  2. включённый режет входы на тихом рынке, где ATR меньше цены круга;
  3. на живом рынке (ATR много больше круга) он не мешает;
  4. ВЫХОД не гейтится никогда: робот в позиции обязан выйти по своему сигналу, иначе
     фильтр держал бы позицию против сигнала (правило семейства, см. долину смерти).
"""
import asyncio
import types

from trader.lab.runtime import BacktestRuntime, Bar
from trader.lab.strategies.library import make_on_bar

SYM = "RIU6"
BASE = {"symbol": SYM, "qty": 1, "fast": 3, "slow": 6, "signal": 3, "spread_pts": 5}


def _bars(step: float, n: int = 400, start: float = 80000.0) -> list[Bar]:
    """Пила с размахом step пунктов: сигнал MACD переворачивается, ATR ≈ step."""
    out, px, t = [], start, 1789000000
    for i in range(n):
        up = (i // 12) % 2 == 0
        nxt = px + (step if up else -step)
        out.append(Bar(t + i * 60, px, max(px, nxt), min(px, nxt), nxt, 100))
        px = nxt
    return out


def _run(bars, params) -> list:
    rt = BacktestRuntime(bars=bars, symbol=SYM, initial_equity=1_000_000.0)
    mod = types.ModuleType("m")
    mod.on_bar = make_on_bar("macd_cross")
    while True:
        asyncio.run(mod.on_bar(rt, params))
        if not rt.advance():
            break
    return [(o.side, o.qty, o.price) for o in rt._orders]


def test_off_changes_nothing():
    bars = _bars(20.0)
    assert _run(bars, {**BASE, "cost_atr": 0}) == _run(bars, dict(BASE))


def test_blocks_entries_when_bar_move_is_smaller_than_round_trip():
    """Круг на RI ≈ 2×(5 пт спреда + 0.0066%×80000 ≈ 5.3) ≈ 20.6 пт: ход 10 пт не окупает."""
    quiet = _bars(10.0)
    assert len(_run(quiet, {**BASE, "cost_atr": 10})) == 0
    assert len(_run(quiet, {**BASE, "cost_atr": 0})) > 0


def test_allows_entries_when_move_is_large():
    loud = _bars(200.0)
    assert len(_run(loud, {**BASE, "cost_atr": 10})) > 0


def test_exit_is_never_gated():
    """Вошли на живом рынке, дальше рынок затих — позиция обязана закрыться по сигналу."""
    bars = _bars(200.0, n=200) + _bars(5.0, n=200, start=80000.0 + 0.0)
    for i, b in enumerate(bars):                      # непрерывное время после склейки
        bars[i] = Bar(1789000000 + i * 60, b.open, b.high, b.low, b.close, b.volume)
    orders = _run(bars, {**BASE, "cost_atr": 30})
    pos = sum(o[1] if o[0] == "buy" else -o[1] for o in orders)
    assert orders and pos == 0, f"позиция не закрыта: {pos}, сделок {len(orders)}"


def test_frozen_market_is_blocked_too():
    """ATR ровно ноль = замерший рынок: фильтр обязан НЕ пускать вход.

    До 20.09.2026 в условии стояло `atrv > 0`, и ровно этот случай проходил мимо
    фильтра. Живой пример: бумажный agent-si2ema 19.09 с 07:00 до 09:29 МСК сделал
    150 филлов по одной цене (лента молчит, бары из одной котировки) и отдал 998 ₽
    комиссии при валовом +37.
    """
    flat = [Bar(1789000000 + i * 60, 80000.0, 80000.0, 80000.0, 80000.0, 0)
            for i in range(300)]
    assert len(_run(flat, {**BASE, "cost_atr": 10})) == 0
    # Без фильтра стратегия на плоских барах сигнала не даёт вовсе — проверяем, что
    # блокировка именно фильтра, а не отсутствия сигнала, на СТУПЕНЬКЕ: один скачок
    # даёт MACD сигнал, но ATR остаётся много меньше цены круга.
    step = flat[:200] + [Bar(flat[199].time + 60 * (i + 1), 80000.0 + 3 * (i + 1),
                             80000.0 + 3 * (i + 1), 80000.0 + 3 * (i + 1),
                             80000.0 + 3 * (i + 1), 0) for i in range(100)]
    assert len(_run(step, {**BASE, "cost_atr": 10})) == 0
    assert len(_run(step, {**BASE, "cost_atr": 0})) > 0
