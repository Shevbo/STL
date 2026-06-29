"""FinamBroker / QuikBroker construction + behaviour with FAKES.

No network, no real orders. Verifies:
  * honest capability claims (which CORE each adapter has / lacks),
  * correct trade-ready status (both are NOT trade-ready by design today),
  * reads map to broker-neutral models,
  * QUIK place/cancel/exec ENQUEUE messages (against a fake server, no send).
"""

from __future__ import annotations

import pytest

from trader.broker.base import (
    Capability,
    OrderRef,
    OrderRequest,
    OrderType,
    Side,
)
from trader.broker.finam import FinamBroker
from trader.broker.quik import QuikBroker


# ============================ FinamBroker ============================

class _FakeSettings:
    finam_api_base_url = "https://api.finam.ru"
    finam_account_id = "ACC-1"
    finam_token_refresh_before_secs = 60
    quik_price_collar_frac = 0.002
    exchange_interface = "finam"


def test_finam_capabilities_and_not_trade_ready():
    broker = FinamBroker(_FakeSettings())
    caps = broker.capabilities()
    # CORE it CAN do:
    for c in (
        Capability.INSTRUMENTS, Capability.ORDER_BOOK, Capability.PLACE_ORDER,
        Capability.POSITIONS, Capability.ACCOUNT, Capability.CONNECTION,
    ):
        assert c in caps
    # Second wave it has:
    assert Capability.QUOTE in caps
    # CORE it does NOT claim (wrapped tx client lacks them):
    assert Capability.CANCEL_ORDER not in caps
    assert Capability.REPLACE_ORDER not in caps
    assert Capability.ORDERS not in caps
    # -> not trade-ready, and missing_core surfaces exactly those three.
    assert not broker.is_trade_ready()
    assert broker.missing_core() == {
        Capability.CANCEL_ORDER, Capability.REPLACE_ORDER, Capability.ORDERS,
    }


def test_finam_construct_no_network():
    # Constructing the broker must not dial anything.
    broker = FinamBroker(_FakeSettings())
    assert broker.name == "finam"
    assert broker._channel is None  # no channel until first md call


class _FakePos:
    async def get_portfolio(self):
        from trader.pos.models import Position as FPos

        return [
            FPos(symbol="RIU6", account_id="ACC-1", side="short", quantity=2,
                 current_price=0, var_margin=0),
        ]

    async def get_account_summary(self):
        from trader.pos.models import AccountSummary

        return AccountSummary(deposit=100, free=80, in_position=20, variation_margin=5)


async def test_finam_positions_signed_and_account():
    broker = FinamBroker(_FakeSettings(), pos=_FakePos())
    positions = await broker.positions()
    assert positions[0].symbol == "RIU6"
    assert positions[0].qty == -2  # short -> negative
    acct = await broker.account()
    assert acct.free == 80.0
    assert acct.margin_used == 20.0


async def test_finam_reconcile_against_fake():
    broker = FinamBroker(_FakeSettings(), pos=_FakePos())
    diff = await broker.reconcile("RIU6", robot_qty=-2)
    assert diff.exchange_qty == -2
    assert diff.matched


# ============================ QuikBroker ============================

class _FakeStore:
    def __init__(self):
        self._secs = [
            {"code": "RIU6", "name": "RTS-9.26", "class_code": "SPBFUT",
             "price_step": 10.0, "step_cost": 1.5},
        ]
        self._books = {
            "RIU6": {
                "code": "RIU6",
                "bids": [{"price": 100000.0, "quantity": 5}],
                "asks": [{"price": 100010.0, "quantity": 7}],
                "received_at_unix_ms": 123,
            }
        }
        self._ticks = {"RIU6": {"code": "RIU6", "last": 100005, "bid": 100000, "ask": 100010}}

    def securities(self, agent_id=None):
        return self._secs

    def order_book(self, code, agent_id=None):
        return self._books.get(code)

    def tick(self, code, agent_id=None):
        return self._ticks.get(code)

    def status(self, agent_id=None):
        return [{"agent_id": "A1", "link": "green", "last_seen_ms": 999,
                 "last_seen_age_ms": 5}]

    def agent_ids(self):
        return ["A1"]


class _FakeOrderStore:
    def __init__(self):
        self.pending = []
        self.placements = 0

    def working_orders(self, agent_id=None):
        return [{
            "client_id": "c1", "code": "RIU6", "side": "buy", "price": 100000.0,
            "quantity": 1, "state": "active", "order_id": "Q-1", "filled": 0,
            "remaining": 1, "ts_unix_ms": 5, "agent_id": "A1",
        }]

    def register_pending(self, agent, client_id, code, side, price, quantity):
        self.pending.append((agent, client_id, code, side, price, quantity))

    def record_placement(self, agent):
        self.placements += 1


class _FakeServer:
    def __init__(self):
        self.enqueued = []

    def enqueue_order(self, agent_id, message):
        self.enqueued.append((agent_id, message))


def _quik(**kw):
    return QuikBroker(_FakeSettings(), **kw)


def test_quik_capabilities_and_not_trade_ready():
    broker = _quik(quik_store=_FakeStore())
    caps = broker.capabilities()
    for c in (
        Capability.INSTRUMENTS, Capability.ORDER_BOOK, Capability.PLACE_ORDER,
        Capability.CANCEL_ORDER, Capability.ORDERS, Capability.CONNECTION,
    ):
        assert c in caps
    assert Capability.MAKER_EXECUTION in caps  # second wave
    # NOT claimed: agent silent on positions/account, native replace not wired.
    assert Capability.POSITIONS not in caps
    assert Capability.ACCOUNT not in caps
    assert Capability.REPLACE_ORDER not in caps
    assert not broker.is_trade_ready()
    assert broker.missing_core() == {
        Capability.POSITIONS, Capability.ACCOUNT, Capability.REPLACE_ORDER,
    }


async def test_quik_reads_map_to_models():
    broker = _quik(quik_store=_FakeStore(), quik_order_store=_FakeOrderStore())
    inst = await broker.instrument("RIU6")
    assert inst.price_step == 10.0 and inst.step_cost == 1.5
    book = await broker.order_book("RIU6")
    assert book.best_bid.price == 100000.0
    assert book.best_ask.price == 100010.0
    quote = await broker.quote("RIU6")
    assert quote.last == 100005.0
    orders = await broker.orders()
    assert orders[0].ref.client_id == "c1"
    assert orders[0].symbol == "RIU6"
    conn = await broker.connection()
    assert conn.broker_up is True


async def test_quik_place_enqueues_no_send():
    store, ost, srv = _FakeStore(), _FakeOrderStore(), _FakeServer()
    broker = _quik(quik_store=store, quik_order_store=ost, quik_server=srv)
    req = OrderRequest(symbol="RIU6", side=Side.BUY, qty=1, price=100000.0,
                       order_type=OrderType.LIMIT, client_id="c9")
    ref = await broker.place_order(req)
    assert ref.client_id == "c9"
    # Enqueued exactly one message; registered pending; recorded placement.
    assert len(srv.enqueued) == 1
    assert srv.enqueued[0][0] == "A1"
    assert srv.enqueued[0][1].HasField("place_order")
    assert ost.pending and ost.placements == 1


async def test_quik_cancel_enqueues():
    srv = _FakeServer()
    broker = _quik(quik_store=_FakeStore(), quik_server=srv)
    await broker.cancel_order(OrderRef(client_id="c9", order_id="Q-1"))
    assert len(srv.enqueued) == 1
    assert srv.enqueued[0][1].HasField("cancel_order")


async def test_quik_start_execution_enqueues():
    srv = _FakeServer()
    broker = _quik(quik_store=_FakeStore(), quik_order_store=_FakeOrderStore(),
                   quik_server=srv)
    req = OrderRequest(symbol="RIU6", side=Side.SELL, qty=2, price=0.0,
                       order_type=OrderType.LIMIT, client_id="e1")
    await broker.start_execution(req, worst_price=99000.0)
    assert srv.enqueued[0][1].HasField("start_execution")


async def test_quik_replace_unsupported():
    from trader.broker.base import UnsupportedCapability

    broker = _quik(quik_store=_FakeStore())
    with pytest.raises(UnsupportedCapability):
        await broker.replace_order(OrderRef(client_id="c9"), new_price=100020.0)
