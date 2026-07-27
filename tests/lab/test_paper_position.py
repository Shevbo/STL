"""Средняя цена бумажной позиции — она же выход из шорта.

Инцидент 2026-07-27: `_paper_position` возвращала avg_price=0 всегда. Тейк шорта
`price <= avg - tp*ATR` при нулевой средней недостижим, поэтому 10 бумажных
роботов стояли в шорте с 18.06 и не могли выйти; тейк лонга наоборот срабатывал
на первом баре. Тест держит правильную лестницу средней.
"""
from __future__ import annotations

import asyncio

from trader.lab.runtime import LiveRuntime


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *_args):
        return self._rows


class _FakePool:
    """Минимальный async-context пул: отдаёт заранее заданные филлы."""

    def __init__(self, rows):
        self._conn = _FakeConn(rows)

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()


def _pos(rows):
    rt = LiveRuntime("r1", _FakePool(rows), paper=True)
    return asyncio.run(rt._paper_position("RIU6"))


def _fill(side, qty, price):
    return {"side": side, "qty": qty, "price": price}


def test_short_ladder_keeps_real_average():
    """Тот самый живой случай: 10 продаж вокруг 103 800 — средняя НЕ ноль."""
    rows = [_fill("sell", 1, 103_800 + i * 10) for i in range(10)]
    p = _pos(rows)
    assert p.side == "short" and p.quantity == 10
    assert float(p.avg_price) == 103_845.0        # среднее 103800..103890


def test_partial_close_keeps_average_full_close_zeroes_it():
    p = _pos([_fill("buy", 2, 100.0), _fill("buy", 2, 120.0), _fill("sell", 1, 130.0)])
    assert p.side == "long" and p.quantity == 3
    assert float(p.avg_price) == 110.0            # частичный выход средней не двигает
    flat = _pos([_fill("buy", 2, 100.0), _fill("sell", 2, 130.0)])
    assert flat.side == "flat" and float(flat.avg_price) == 0.0


def test_reverse_through_zero_rebases_average():
    p = _pos([_fill("buy", 2, 100.0), _fill("sell", 5, 90.0)])
    assert p.side == "short" and p.quantity == 3
    assert float(p.avg_price) == 90.0             # новая позиция — новая средняя


def test_no_pool_is_flat_not_crash():
    rt = LiveRuntime("r1", None, paper=True)
    p = asyncio.run(rt._paper_position("RIU6"))
    assert p.side == "flat" and p.quantity == 0
