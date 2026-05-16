# tests/md/test_ws_client.py
"""
Tests for WsSession happy-path only (no reconnect logic — see test_reconnect.py).
WS protocol TODO markers are present — update when actual Finam API format is confirmed.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from trader.md.ws_client import WsSession


def make_fake_ws(recv_messages: list[dict]) -> AsyncMock:
    """Build a mock WebSocket that yields given messages via __aiter__."""
    ws = AsyncMock()
    # recv() returns auth_ack on first call (used during connect)
    ws.recv = AsyncMock(return_value=orjson.dumps({"type": "auth_ack"}))
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    # __aiter__ yields subsequent frames via an async generator
    encoded = [orjson.dumps(m) for m in recv_messages]

    async def _aiter():
        for f in encoded:
            yield f

    ws.__aiter__ = MagicMock(return_value=_aiter())
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    return ws


@pytest.fixture
def fake_ws():
    return make_fake_ws([])


async def test_connect_sends_auth_message(fake_ws):
    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=fake_ws)):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok123"))
        await session.close()

    sent = orjson.loads(fake_ws.send.call_args_list[0][0][0])
    # TODO: verify exact auth message format from Finam API docs
    assert sent["type"] == "auth"
    assert sent["token"] == "tok123"


async def test_connect_sets_connected_true(fake_ws):
    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=fake_ws)):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"))
        assert session.connected is True
        await session.close()


async def test_subscribe_sends_subscribe_message(fake_ws):
    # subscribe_ack must appear in ctrl queue before subscribe() returns
    ack = {"type": "subscribe_ack", "symbol": "GZM6@RTSX"}
    fake_ws_with_ack = make_fake_ws([ack])

    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=fake_ws_with_ack)):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"))
        await session.subscribe("GZM6@RTSX")
        await session.close()

    calls = [orjson.loads(c[0][0]) for c in fake_ws_with_ack.send.call_args_list]
    sub_msg = next(m for m in calls if m.get("type") == "subscribe")
    # TODO: verify exact subscribe message format from Finam API docs
    assert sub_msg["symbol"] == "GZM6@RTSX"


async def test_iter_quotes_yields_quote_frames():
    quote_frame = {
        "type": "quote",
        "symbol": "GZM6@RTSX",
        "bid": "100.0", "bid_size": 1,
        "ask": "100.1", "ask_size": 1,
        "last": "100.05", "last_size": 1,
        "timestamp": "2026-05-15T10:00:00Z",
    }
    fake_ws_with_quote = make_fake_ws([quote_frame])

    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=fake_ws_with_quote)):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"))

        received = []
        async for raw in session.iter_quotes():
            received.append(raw)
            break  # stop after first

        await session.close()

    assert len(received) == 1
    assert received[0]["symbol"] == "GZM6@RTSX"


async def test_close_sets_connected_false(fake_ws):
    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=fake_ws)):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"))
        await session.close()
        assert session.connected is False


async def test_messages_received_counter_increments():
    frames = [
        {"type": "quote", "symbol": "S", "bid": "1", "ask": "1", "last": "1",
         "bid_size": 0, "ask_size": 0, "last_size": 0, "timestamp": "2026-05-15T10:00:00Z"},
        {"type": "quote", "symbol": "S", "bid": "2", "ask": "2", "last": "2",
         "bid_size": 0, "ask_size": 0, "last_size": 0, "timestamp": "2026-05-15T10:00:01Z"},
    ]
    fake = make_fake_ws(frames)

    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=fake)):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"))
        # Drain the data queue
        count = 0
        async for _ in session.iter_quotes():
            count += 1
            if count == 2:
                break
        await session.close()

    assert session.messages_received >= 2
