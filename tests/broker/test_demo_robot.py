"""Demo robot runs through the contract only, switching finam<->quik by config.

DRY-RUN: no real order is sent. Uses fakes injected through the registry factory.
"""

from __future__ import annotations

from trader.broker import registry
from trader.broker.base import CORE_CAPABILITIES, BrokerInterface, Capability
from trader.broker.demo_robot import run_demo
from trader.broker.base import (
    Account,
    BookLevel,
    ConnState,
    Instrument,
    OrderBook,
    Position,
)


class _Settings:
    def __init__(self, interface):
        self.exchange_interface = interface
        self.quik_price_collar_frac = 0.002


class _FakeBroker(BrokerInterface):
    """A FULL (trade-ready) fake broker for the demo, no network."""

    name = "fakefull"

    def __init__(self, settings, **inject):
        self.settings = settings

    def capabilities(self):
        return set(CORE_CAPABILITIES)

    async def connection(self):
        return ConnState(broker_up=True, exchange_up=True)

    async def instrument(self, symbol):
        return Instrument(symbol=symbol, price_step=10.0, step_cost=1.5)

    async def order_book(self, symbol, depth=10):
        return OrderBook(symbol=symbol, bids=[BookLevel(100000.0, 5)],
                         asks=[BookLevel(100010.0, 7)])

    async def account(self):
        return Account(free=80.0, margin_used=20.0)

    async def position(self, symbol):
        return Position(symbol=symbol, qty=0)


async def test_demo_robot_dry_run_full_broker():
    registry.register("fakefull")(lambda s, **k: _FakeBroker(s, **k))
    try:
        report = await run_demo(_Settings("fakefull"), "RIU6", dry_run=True)
    finally:
        registry._REGISTRY.pop("fakefull", None)

    assert report["broker"] == "fakefull"
    assert report["trade_ready"] is True
    assert report["book"]["best_bid"] == 100000.0
    assert report["account"]["free"] == 80.0
    assert report["reconcile"]["matched"] is True
    # DRY-RUN: it only describes where it WOULD place + a native replace line.
    assert any("DRY-RUN place" in a for a in report["actions"])
    assert any("replace" in a.lower() for a in report["actions"])


class _PartialBroker(BrokerInterface):
    """Missing ACCOUNT + POSITIONS — robot must degrade gracefully in dry-run."""

    name = "fakepartial"

    def __init__(self, settings, **inject):
        pass

    def capabilities(self):
        return set(CORE_CAPABILITIES) - {Capability.ACCOUNT, Capability.POSITIONS}

    async def connection(self):
        return ConnState(broker_up=True, exchange_up=True)

    async def instrument(self, symbol):
        return Instrument(symbol=symbol, price_step=10.0)

    async def order_book(self, symbol, depth=10):
        return OrderBook(symbol=symbol, bids=[BookLevel(100000.0, 5)], asks=[])


async def test_demo_robot_degrades_when_caps_missing():
    registry.register("fakepartial")(lambda s, **k: _PartialBroker(s, **k))
    try:
        report = await run_demo(_Settings("fakepartial"), "RIU6", dry_run=True)
    finally:
        registry._REGISTRY.pop("fakepartial", None)

    # Bypassed the gate (dry_run) but reports it is not trade-ready.
    assert report["trade_ready"] is False
    assert report["account"] is None       # degraded, not crashed
    assert report["reconcile"] is None
