"""Tests for the STL-side QUIK agent gRPC server (sprint02 Phase 1).

A fake agent opens the Session bidi stream over a real in-process grpc.aio
channel, sends Register + Tick + OrderBook, and we assert:
  * the store reflects each frame,
  * each frame is Ack'd with its seq,
  * a bad / missing Bearer is rejected (UNAUTHENTICATED).

Read-only: no order transactions anywhere here. Marked non-integration
(no marker => runs by default; needs no credentials).
"""

import asyncio

import grpc

import trader.quik  # noqa: F401 — sets up pb import path
from shectory.quik.v1 import quik_agent_pb2 as pb
from shectory.quik.v1 import quik_agent_pb2_grpc as pb_grpc
from trader.quik.server import QuikAgentLinkServicer, verify_agent_token
from trader.quik.store import QuikAgentStore

AGENT_SECRET = "test-agent-secret"
PORTAL_SECRET = "test-portal-secret"


async def _start_server(store: QuikAgentStore):
    servicer = QuikAgentLinkServicer(store, AGENT_SECRET, PORTAL_SECRET)
    server = grpc.aio.server()
    pb_grpc.add_QuikAgentLinkServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port, servicer


def _agent_frames():
    yield pb.AgentMessage(
        seq=1,
        register=pb.Register(
            agent_version="1.2.3", host_name="WIN-QUIK01",
            quik_data_root="C:/QUIK", timezone_offset_min=180, build_rev=42,
        ),
    )
    yield pb.AgentMessage(
        seq=2,
        tick=pb.MarketDataTick(
            code="RIU6", last=100000.0, bid=99990.0, ask=100010.0,
            open_interest=12345, exchange_ts_unix_ms=1, received_at_unix_ms=2,
        ),
    )
    yield pb.AgentMessage(
        seq=3,
        order_book=pb.OrderBook(
            code="RIU6",
            bids=[pb.OrderBookLevel(price=99990.0, quantity=5)],
            asks=[pb.OrderBookLevel(price=100010.0, quantity=7)],
            received_at_unix_ms=3,
        ),
    )


async def _run_session(port: int, metadata):
    """Open the stream, push the 3 frames, collect Acks until all seen."""
    acks: list[int] = []
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        stub = pb_grpc.QuikAgentLinkStub(channel)

        async def gen():
            for fr in _agent_frames():
                yield fr
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.2)  # keep stream open so server can Ack frame 3

        call = stub.Session(gen(), metadata=metadata)
        try:
            async for msg in call:
                if msg.WhichOneof("payload") == "ack":
                    acks.append(msg.ack.ack_seq)
                    if len(acks) >= 3:
                        break
        finally:
            call.cancel()
    return acks


async def _expect_reject(port: int, metadata) -> grpc.StatusCode:
    """Open the stream with no request frames and read the terminal status.

    Sending request frames into an already-rejected stream can surface as a
    client-side INTERNAL send error (it races the server's status), which is a
    test artefact, not server behaviour. Here we send nothing and only assert on
    the RPC's terminal status code.
    """
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        stub = pb_grpc.QuikAgentLinkStub(channel)

        async def empty():
            if False:
                yield  # make this an async generator that yields nothing
            await asyncio.sleep(0.5)

        call = stub.Session(empty(), metadata=metadata)
        try:
            async for _ in call:
                pass
        except grpc.aio.AioRpcError as exc:
            return exc.code()
        return await call.code()


async def test_session_register_tick_orderbook_updates_store():
    store = QuikAgentStore(link_fresh_sec=15)
    server, port, _servicer = await _start_server(store)
    try:
        acks = await _run_session(port, [("authorization", f"Bearer {AGENT_SECRET}")])
    finally:
        await server.stop(0)

    assert acks == [1, 2, 3]

    # Register migrates the agent id to host_name.
    ids = store.agent_ids()
    assert "WIN-QUIK01" in ids
    agent_id = "WIN-QUIK01"

    status = store.status(agent_id)[0]
    assert status["register"]["agent_version"] == "1.2.3"
    assert status["register"]["build_rev"] == 42
    assert status["link"] == "green"
    assert status["last_seq"] == 3

    tick = store.tick("RIU6", agent_id)
    assert tick is not None
    assert tick["last"] == 100000.0
    assert tick["open_interest"] == 12345

    ob = store.order_book("RIU6", agent_id)
    assert ob is not None
    assert ob["bids"][0]["price"] == 99990.0
    assert ob["asks"][0]["quantity"] == 7


async def test_session_rejects_missing_bearer():
    store = QuikAgentStore()
    server, port, _servicer = await _start_server(store)
    try:
        code = await _expect_reject(port, [])  # no authorization metadata
        assert code == grpc.StatusCode.UNAUTHENTICATED
    finally:
        await server.stop(0)

    assert store.agent_ids() == []


async def test_session_rejects_bad_bearer():
    store = QuikAgentStore()
    server, port, _servicer = await _start_server(store)
    try:
        code = await _expect_reject(port, [("authorization", "Bearer wrong-token")])
        assert code == grpc.StatusCode.UNAUTHENTICATED
    finally:
        await server.stop(0)

    assert store.agent_ids() == []


def test_verify_agent_token_shapes():
    # opaque shared-secret bearer
    assert verify_agent_token(AGENT_SECRET, AGENT_SECRET, PORTAL_SECRET) == "quik-agent"
    # missing / wrong
    assert verify_agent_token(None, AGENT_SECRET, PORTAL_SECRET) is None
    assert verify_agent_token("nope", AGENT_SECRET, PORTAL_SECRET) is None
    # signed session token (portal secret), verified like the Showcase link
    from trader.auth.portal import make_session_token
    tok = make_session_token("klod-stl", PORTAL_SECRET)
    assert verify_agent_token(tok, AGENT_SECRET, PORTAL_SECRET) == "klod-stl"
