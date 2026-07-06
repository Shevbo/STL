"""STL mirror of the agent's local-showcase status snapshot (Task 11).

Covers:
  * a fake agent streaming a status_snapshot frame -> store reflects it,
  * store set/get verbatim roundtrip + received_at stamp,
  * malformed JSON is dropped (store, never the stream handler, crashes),
  * the FastAPI route GET /api/v1/quik/agent-local-status (agent_id optional,
    unknown-agent empty shape).

No order transactions. Marked non-integration (runs by default, no credentials).
"""

import asyncio
import json

import grpc
from fastapi import FastAPI
from fastapi.testclient import TestClient

import trader.quik  # noqa: F401 — sets up pb import path
from shectory.quik.v1 import quik_agent_pb2 as pb
from shectory.quik.v1 import quik_agent_pb2_grpc as pb_grpc
from trader.api.quik_robots import router as quik_robots_router
from trader.quik.server import QuikAgentLinkServicer
from trader.quik.store import QuikAgentStore

AGENT_SECRET = "test-agent-secret"
PORTAL_SECRET = "test-portal-secret"


# ---- store unit tests ----

def test_store_set_get_agent_status_roundtrip():
    store = QuikAgentStore()
    payload = {"headline": "RIU6 long", "pnl_points": 12.5, "nested": {"a": 1}}
    store.set_agent_status("a1", json.dumps(payload), generated_at_ms=1234)

    got = store.agent_status("a1")
    assert got is not None
    assert got["headline"] == "RIU6 long"
    assert got["pnl_points"] == 12.5
    assert got["nested"] == {"a": 1}
    assert "_received_at_ms" in got
    assert isinstance(got["_received_at_ms"], int)


def test_store_agent_status_unknown_agent_is_none():
    store = QuikAgentStore()
    assert store.agent_status("nope") is None


def test_store_agent_status_malformed_json_is_dropped():
    store = QuikAgentStore()
    store.set_agent_status("a1", "{not valid json", generated_at_ms=1)
    assert store.agent_status("a1") is None


def test_store_agent_status_non_object_json_is_dropped():
    """A JSON array/scalar is valid JSON but not the expected shape — drop it."""
    store = QuikAgentStore()
    store.set_agent_status("a1", "[1, 2, 3]", generated_at_ms=1)
    assert store.agent_status("a1") is None


def test_store_agent_status_does_not_leak_between_agents():
    store = QuikAgentStore()
    store.set_agent_status("a1", json.dumps({"who": "a1"}), generated_at_ms=1)
    store.set_agent_status("a2", json.dumps({"who": "a2"}), generated_at_ms=1)
    assert store.agent_status("a1")["who"] == "a1"
    assert store.agent_status("a2")["who"] == "a2"


def test_store_agent_status_picks_single_agent_when_unambiguous():
    store = QuikAgentStore()
    store.set_agent_status("only", json.dumps({"ok": True}), generated_at_ms=1)
    assert store.agent_status()["ok"] is True


# ---- gRPC stream test: a fake agent pushes a status_snapshot ----

async def _start_server(store: QuikAgentStore):
    servicer = QuikAgentLinkServicer(store, AGENT_SECRET, PORTAL_SECRET)
    server = grpc.aio.server()
    pb_grpc.add_QuikAgentLinkServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port


def _frames(status_json: str):
    yield pb.AgentMessage(
        seq=1,
        register=pb.Register(host_name="WIN-QUIK01", agent_version="1.0"),
    )
    yield pb.AgentMessage(
        seq=2,
        status_snapshot=pb.AgentStatusSnapshot(
            status_json=status_json, generated_at_unix_ms=9999,
        ),
    )


async def _run_session(port: int, status_json: str):
    acks: list[int] = []
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        stub = pb_grpc.QuikAgentLinkStub(channel)

        async def gen():
            for fr in _frames(status_json):
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


async def test_session_status_snapshot_updates_store():
    store = QuikAgentStore(link_fresh_sec=15)
    server, port = await _start_server(store)
    payload = {"showcase": {"robots": 3}, "generated": True}
    try:
        acks = await _run_session(port, json.dumps(payload))
    finally:
        await server.stop(0)

    assert acks == [1, 2]
    got = store.agent_status("WIN-QUIK01")
    assert got is not None
    assert got["showcase"] == {"robots": 3}
    assert got["generated"] is True
    assert "_received_at_ms" in got


async def test_session_malformed_status_snapshot_does_not_crash_stream():
    """A bad status_json frame must not break the session (or later frames)."""
    store = QuikAgentStore(link_fresh_sec=15)
    server, port = await _start_server(store)
    try:
        acks = await _run_session(port, "{not json")
    finally:
        await server.stop(0)

    assert acks == [1, 2]  # frame 2 still Ack'd even though the store dropped it
    assert store.agent_status("WIN-QUIK01") is None


# ---- FastAPI route test (dev-bypass auth, injected store) ----

def _client_with_store(store: QuikAgentStore, monkeypatch) -> TestClient:
    monkeypatch.setenv("SHECTORY_AUTH_DEV_BYPASS", "1")
    app = FastAPI()
    app.include_router(quik_robots_router)

    class _Settings:
        shectory_auth_bridge_secret = ""  # empty -> dev bypass path

    app.state.settings = _Settings()
    app.state.quik_store = store
    return TestClient(app)


def test_route_agent_local_status_verbatim(monkeypatch):
    store = QuikAgentStore()
    payload = {"headline": "RIU6 long", "pnl_points": 12.5}
    store.set_agent_status("a1", json.dumps(payload), generated_at_ms=1)
    client = _client_with_store(store, monkeypatch)

    r = client.get("/api/v1/quik/agent-local-status", params={"agent_id": "a1"})
    assert r.status_code == 200
    body = r.json()
    assert body["headline"] == "RIU6 long"
    assert body["pnl_points"] == 12.5
    assert "_received_at_ms" in body


def test_route_agent_local_status_no_agent_id_picks_single(monkeypatch):
    store = QuikAgentStore()
    store.set_agent_status("only", json.dumps({"ok": True}), generated_at_ms=1)
    client = _client_with_store(store, monkeypatch)

    r = client.get("/api/v1/quik/agent-local-status")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_route_agent_local_status_unknown_agent_empty_shape(monkeypatch):
    store = QuikAgentStore()
    client = _client_with_store(store, monkeypatch)

    r = client.get("/api/v1/quik/agent-local-status", params={"agent_id": "nope"})
    assert r.status_code == 200
    assert r.json() == {"status": None, "_received_at_ms": None}
