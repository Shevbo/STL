"""Contract-level behaviour: capability enforcement + reconcile() math.

No network, no orders.
"""

from __future__ import annotations

import pytest

from trader.broker.base import (
    BrokerInterface,
    Capability,
    OrderRef,
    Position,
    UnsupportedCapability,
    reconcile_position,
)


class _OnlyBook(BrokerInterface):
    name = "onlybook"

    def capabilities(self):
        return {Capability.ORDER_BOOK}


async def test_unclaimed_capability_raises():
    broker = _OnlyBook()
    # PLACE_ORDER is not claimed -> the base guard raises UnsupportedCapability.
    with pytest.raises(UnsupportedCapability) as exc:
        await broker.place_order(None)  # type: ignore[arg-type]
    assert exc.value.capability is Capability.PLACE_ORDER


async def test_supports_reflects_capabilities():
    broker = _OnlyBook()
    assert broker.supports(Capability.ORDER_BOOK)
    assert not broker.supports(Capability.PLACE_ORDER)


def test_reconcile_helper_math():
    # exchange long 3, robot thinks 1 -> delta +2, not matched.
    diff = reconcile_position(Position(symbol="RIU6", qty=3), robot_qty=1, symbol="RIU6")
    assert diff.exchange_qty == 3
    assert diff.robot_qty == 1
    assert diff.delta == 2
    assert not diff.matched


def test_reconcile_helper_flat_and_matched():
    diff = reconcile_position(None, robot_qty=0, symbol="RIU6")
    assert diff.exchange_qty == 0
    assert diff.delta == 0
    assert diff.matched


class _Positioned(BrokerInterface):
    name = "positioned"

    def capabilities(self):
        return {Capability.POSITIONS}

    async def position(self, symbol):
        return Position(symbol=symbol, qty=-2)


async def test_reconcile_method_uses_position():
    broker = _Positioned()
    diff = await broker.reconcile("RIU6", robot_qty=0)
    assert diff.exchange_qty == -2
    assert diff.delta == -2
    assert not diff.matched


async def test_replace_unsupported_by_default():
    broker = _OnlyBook()
    with pytest.raises(UnsupportedCapability):
        await broker.replace_order(OrderRef(client_id="x"), new_price=100.0)
