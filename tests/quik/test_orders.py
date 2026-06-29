"""Tests for the STL-side QUIK orders (sprint02 Phase 2). HUMAN-INITIATED only.

Covers:
  * each hard limit rejection (whitelist, per-order qty, working total, collar,
    daily cap) + the master-flag-off rejection,
  * a place -> OrderUpdate round-trip through a fake agent over a real in-process
    grpc.aio stream (the order leaves STL as PlaceOrder; the agent's OrderUpdate
    lands in the OrderStore),
  * the kill-switch path (blocks new placements + enqueues KillSwitch).

No order ever auto-originates (Guard 3): every placement is an explicit API call.
Non-integration (runs by default, needs no credentials).
"""

import asyncio

import grpc
import pytest

import trader.quik  # noqa: F401 — pb import path
from shectory.quik.v1 import quik_agent_pb2 as pb
from shectory.quik.v1 import quik_agent_pb2_grpc as pb_grpc
from trader.quik import orders as order_msgs
from trader.quik.limits import (
    LimitError,
    OrderLimits,
    validate_place,
)
from trader.quik.orders import OrderStore
from trader.quik.server import QuikAgentLinkServicer
from trader.quik.store import QuikAgentStore

AGENT_SECRET = "test-agent-secret"
PORTAL_SECRET = "test-portal-secret"


def _limits(**over) -> OrderLimits:
    base = dict(
        trading_enabled=True,
        max_contracts_per_order=2,
        max_working_contracts=2,
        price_collar_frac=0.002,
        instrument_whitelist=("RIU6",),
        daily_order_cap=50,
    )
    base.update(over)
    return OrderLimits(**base)


def _ok_place(lim, **over):
    args = dict(
        code="RIU6", quantity=1, collar=0.002,
        current_working=0, placed_today=0,
    )
    args.update(over)
    validate_place(lim, **args)


# ---- limit rejections (each limit) ----

def test_master_flag_off_rejects():
    lim = _limits(trading_enabled=False)
    with pytest.raises(LimitError, match="отключена"):
        _ok_place(lim)


def test_whitelist_rejects_unlisted_instrument():
    lim = _limits()
    with pytest.raises(LimitError, match="белом списке"):
        _ok_place(lim, code="SiU6")


def test_quantity_over_per_order_cap_rejects():
    lim = _limits()
    with pytest.raises(LimitError, match="превышает лимит на заявку"):
        _ok_place(lim, quantity=3)


def test_zero_quantity_rejects():
    lim = _limits()
    with pytest.raises(LimitError, match="> 0"):
        _ok_place(lim, quantity=0)


def test_working_total_over_cap_rejects():
    lim = _limits(max_working_contracts=2)
    # 2 already resting + 1 new = 3 > 2
    with pytest.raises(LimitError, match="в работе"):
        _ok_place(lim, quantity=1, current_working=2)


def test_collar_over_max_rejects():
    lim = _limits()
    with pytest.raises(LimitError, match="оллар"):
        _ok_place(lim, collar=0.01)


def test_daily_cap_rejects():
    lim = _limits(daily_order_cap=5)
    with pytest.raises(LimitError, match="дневной лимит"):
        _ok_place(lim, placed_today=5)


def test_valid_place_passes_all_limits():
    _ok_place(_limits())  # no raise


# ---- OrderStore bookkeeping ----

def test_order_store_working_contracts_and_daily_cap():
    ost = OrderStore()
    aid = "WIN-QUIK01"
    assert ost.placed_today(aid) == 0
    assert ost.working_contracts(aid) == 0

    ost.register_pending(aid, "c1", "RIU6", "buy", 100000.0, 2)
    ost.record_placement(aid)
    assert ost.placed_today(aid) == 1
    # pending counts as working (2 unfilled)
    assert ost.working_contracts(aid) == 2

    # an OrderUpdate FILLED clears the working budget
    ost.apply_order_update(aid, pb.OrderUpdate(
        client_id="c1", order_id="42", code="RIU6", side=pb.SIDE_BUY,
        state=pb.ORDER_STATE_FILLED, price=100000.0, quantity=2, filled=2,
    ))
    assert ost.working_contracts(aid) == 0
    rows = ost.working_orders(aid)
    assert rows[0]["state"] == "filled"
    assert rows[0]["order_id"] == "42"
    assert rows[0]["remaining"] == 0


def test_kill_switch_blocks_and_clears():
    ost = OrderStore()
    aid = "WIN-QUIK01"
    assert ost.is_blocked(aid) is False
    ost.set_blocked(aid, True)
    assert ost.is_blocked(aid) is True
    ost.set_blocked(aid, False)
    assert ost.is_blocked(aid) is False


# ---- message builders ----

def test_build_place_order_message_shape():
    msg = order_msgs.build_place_order("c1", "RIU6", "sell", 99990.0, 1, 0.002)
    assert msg.WhichOneof("payload") == "place_order"
    assert msg.place_order.side == pb.SIDE_SELL
    assert msg.place_order.code == "RIU6"
    assert msg.place_order.quantity == 1


def test_build_kill_switch_message_shape():
    msg = order_msgs.build_kill_switch("panic")
    assert msg.WhichOneof("payload") == "kill_switch"
    assert msg.kill_switch.reason == "panic"


# ---- place -> OrderUpdate round-trip via a fake agent stream ----

async def _start_server(store: QuikAgentStore, order_store: OrderStore):
    servicer = QuikAgentLinkServicer(
        store, AGENT_SECRET, PORTAL_SECRET, order_store=order_store)
    server = grpc.aio.server()
    pb_grpc.add_QuikAgentLinkServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port, servicer


async def test_place_to_order_update_round_trip():
    """Operator places -> STL enqueues PlaceOrder -> the fake agent receives it and
    replies OrderUpdate(ACTIVE) -> the OrderStore reflects the working order."""
    store = QuikAgentStore(link_fresh_sec=15)
    order_store = OrderStore()
    server, port, servicer = await _start_server(store, order_store)

    received_place: list[pb.PlaceOrder] = []
    agent_id_seen: list[str] = []

    async def fake_agent():
        """Dial in, register, then act on STL->agent order messages."""
        acks = 0
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.QuikAgentLinkStub(channel)
            to_send: asyncio.Queue = asyncio.Queue()
            # frame 1: register (so agent_id becomes the host name)
            await to_send.put(pb.AgentMessage(
                seq=1, register=pb.Register(host_name="WIN-QUIK01")))

            async def gen():
                # send register, then a heartbeat to keep flushing, then react
                while True:
                    fr = await to_send.get()
                    if fr is None:
                        return
                    yield fr

            call = stub.Session(gen(), metadata=[
                ("authorization", f"Bearer {AGENT_SECRET}")])
            # heartbeat after register so the server flushes any queued order
            await asyncio.sleep(0.05)
            await to_send.put(pb.AgentMessage(seq=2, heartbeat=pb.Heartbeat()))
            async for msg in call:
                kind = msg.WhichOneof("payload")
                if kind == "ack":
                    acks += 1
                    continue
                if kind == "place_order":
                    received_place.append(msg.place_order)
                    # The agent replies with an ACTIVE OrderUpdate.
                    await to_send.put(pb.AgentMessage(
                        seq=10,
                        order_update=pb.OrderUpdate(
                            client_id=msg.place_order.client_id,
                            order_id="ORD-1",
                            code=msg.place_order.code,
                            side=msg.place_order.side,
                            state=pb.ORDER_STATE_ACTIVE,
                            price=msg.place_order.price,
                            quantity=msg.place_order.quantity,
                            filled=0,
                        ),
                    ))
                    # keep heartbeating so the update frame is processed + acked
                    await asyncio.sleep(0.05)
                    await to_send.put(pb.AgentMessage(seq=11, heartbeat=pb.Heartbeat()))
                    await asyncio.sleep(0.2)
                    await to_send.put(None)
                    return

    agent_task = asyncio.ensure_future(fake_agent())
    try:
        # wait for register to land, then enqueue a PlaceOrder (operator action).
        for _ in range(50):
            if "WIN-QUIK01" in store.agent_ids():
                break
            await asyncio.sleep(0.02)
        assert "WIN-QUIK01" in store.agent_ids()
        agent_id_seen.append("WIN-QUIK01")

        msg = order_msgs.build_place_order(
            "cli-1", "RIU6", "buy", 100000.0, 1, 0.002)
        order_store.register_pending(
            "WIN-QUIK01", "cli-1", "RIU6", "buy", 100000.0, 1)
        order_store.record_placement("WIN-QUIK01")
        servicer.enqueue_order("WIN-QUIK01", msg)

        await asyncio.wait_for(agent_task, timeout=5)
    finally:
        if not agent_task.done():
            agent_task.cancel()
        await server.stop(0)

    # The agent received exactly the order STL sent.
    assert len(received_place) == 1
    assert received_place[0].client_id == "cli-1"
    assert received_place[0].side == pb.SIDE_BUY

    # The OrderStore reflects the agent's ACTIVE update.
    rows = order_store.working_orders("WIN-QUIK01")
    assert len(rows) == 1
    assert rows[0]["client_id"] == "cli-1"
    assert rows[0]["state"] == "active"
    assert rows[0]["order_id"] == "ORD-1"
    assert order_store.working_contracts("WIN-QUIK01") == 1
