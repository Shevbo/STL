"""Tests for the generic RawTable passthrough (sprint02, additive, read-only).

Covers:
  * a fake agent streaming a RawTable frame -> store reflects it,
  * the store getters (list_raw_tables / get_raw_table),
  * the FastAPI routes GET /api/v1/quik/tables and /tables/{name}.

No order transactions. Marked non-integration (runs by default, no credentials).
"""

import asyncio

import grpc
from fastapi import FastAPI
from fastapi.testclient import TestClient

import trader.quik  # noqa: F401 — sets up pb import path
from shectory.quik.v1 import quik_agent_pb2 as pb
from shectory.quik.v1 import quik_agent_pb2_grpc as pb_grpc
from trader.api.quik_routes import router as quik_router
from trader.quik.server import QuikAgentLinkServicer
from trader.quik.store import QuikAgentStore

AGENT_SECRET = "test-agent-secret"
PORTAL_SECRET = "test-portal-secret"


# ---- store unit tests ----

def test_store_set_get_raw_table():
    store = QuikAgentStore()
    store.set_raw_table(
        "a1", "deals",
        ["time", "code", "price"],
        [["10:00", "RIU6", "100000"], ["10:01", "RIU6", "100010"]],
        received_at_unix_ms=1234,
    )

    summaries = store.list_raw_tables("a1")
    assert len(summaries) == 1
    s = summaries[0]
    assert s["name"] == "deals"
    assert s["columns_count"] == 3
    assert s["rows_count"] == 2
    assert s["received_at_unix_ms"] == 1234

    full = store.get_raw_table("deals", "a1")
    assert full is not None
    assert full["columns"] == ["time", "code", "price"]
    assert full["rows"][1] == ["10:01", "RIU6", "100010"]
    assert full["received_at_unix_ms"] == 1234


def test_store_get_raw_table_picks_single_agent():
    store = QuikAgentStore()
    store.set_raw_table("only", "t", ["c"], [["v"]], 1)
    # agent_id omitted -> single agent is unambiguous
    assert store.get_raw_table("t") is not None
    assert store.get_raw_table("missing") is None


def test_store_ignores_unnamed_table():
    store = QuikAgentStore()
    store.set_raw_table("a1", "", ["c"], [["v"]], 1)
    assert store.list_raw_tables("a1") == []


# ---- gRPC stream test: a fake agent pushes a RawTable ----

async def _start_server(store: QuikAgentStore):
    servicer = QuikAgentLinkServicer(store, AGENT_SECRET, PORTAL_SECRET)
    server = grpc.aio.server()
    pb_grpc.add_QuikAgentLinkServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port


def _frames():
    yield pb.AgentMessage(
        seq=1,
        register=pb.Register(host_name="WIN-QUIK01", agent_version="1.0"),
    )
    yield pb.AgentMessage(
        seq=2,
        raw_table=pb.RawTable(
            name="deals",
            columns=["time", "code", "price"],
            rows=[
                pb.TableRow(cells=["10:00", "RIU6", "100000"]),
                pb.TableRow(cells=["10:01", "RIU6", "100010"]),
            ],
            received_at_unix_ms=7777,
        ),
    )


async def _run_session(port: int):
    acks: list[int] = []
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        stub = pb_grpc.QuikAgentLinkStub(channel)

        async def gen():
            for fr in _frames():
                yield fr
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.2)

        call = stub.Session(gen(), metadata=[("authorization", f"Bearer {AGENT_SECRET}")])
        try:
            async for msg in call:
                if msg.WhichOneof("payload") == "ack":
                    acks.append(msg.ack.ack_seq)
                    if len(acks) >= 2:
                        break
        finally:
            call.cancel()
    return acks


async def test_session_raw_table_updates_store():
    store = QuikAgentStore(link_fresh_sec=15)
    server, port = await _start_server(store)
    try:
        acks = await _run_session(port)
    finally:
        await server.stop(0)

    assert acks == [1, 2]
    full = store.get_raw_table("deals", "WIN-QUIK01")
    assert full is not None
    assert full["columns"] == ["time", "code", "price"]
    assert len(full["rows"]) == 2
    assert full["rows"][0] == ["10:00", "RIU6", "100000"]
    assert full["received_at_unix_ms"] == 7777


# ---- FastAPI route test (dev-bypass auth, injected store) ----

def _client_with_store(store: QuikAgentStore, monkeypatch) -> TestClient:
    monkeypatch.setenv("SHECTORY_AUTH_DEV_BYPASS", "1")
    app = FastAPI()
    app.include_router(quik_router)

    class _Settings:
        shectory_auth_bridge_secret = ""  # empty -> dev bypass path
        quik_agent_enabled = True
        quik_agent_grpc_listen = "127.0.0.1:0"

    app.state.settings = _Settings()
    app.state.quik_store = store
    return TestClient(app)


def test_routes_tables_list_and_get(monkeypatch):
    store = QuikAgentStore()
    store.set_raw_table(
        "a1", "deals",
        ["time", "code"], [["10:00", "RIU6"]],
        received_at_unix_ms=42,
    )
    client = _client_with_store(store, monkeypatch)

    r = client.get("/api/v1/quik/tables")
    assert r.status_code == 200
    tables = r.json()["tables"]
    assert len(tables) == 1
    assert tables[0]["name"] == "deals"
    assert tables[0]["rows_count"] == 1

    r2 = client.get("/api/v1/quik/tables/deals")
    assert r2.status_code == 200
    body = r2.json()
    assert body["columns"] == ["time", "code"]
    assert body["rows"] == [["10:00", "RIU6"]]
    assert body["received_at_unix_ms"] == 42

    r3 = client.get("/api/v1/quik/tables/nope")
    assert r3.status_code == 404
