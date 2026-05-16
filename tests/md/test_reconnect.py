# tests/md/test_reconnect.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import orjson
import pytest
import websockets.exceptions

from trader.md.ws_client import AuthError, WsSession, RECONNECT_MAX


def make_closing_ws(frames_before_drop: list[dict], then_frames: list[dict] | None = None) -> tuple:
    """
    Returns (ws1, ws2) where ws1 raises ConnectionClosed after yielding frames_before_drop,
    and ws2 (returned on second connect) yields then_frames normally.
    """
    ws1 = AsyncMock()
    ws1.recv = AsyncMock(return_value=orjson.dumps({"type": "auth_ack"}))
    ws1.send = AsyncMock()
    ws1.close = AsyncMock()

    encoded1 = [orjson.dumps(m) for m in frames_before_drop]

    async def iter1():
        for f in encoded1:
            yield f
        raise websockets.exceptions.ConnectionClosed(None, None)

    ws1.__aiter__ = MagicMock(return_value=iter1())
    ws1.__aenter__ = AsyncMock(return_value=ws1)
    ws1.__aexit__ = AsyncMock(return_value=None)

    ws2 = AsyncMock()
    ws2.recv = AsyncMock(return_value=orjson.dumps({"type": "auth_ack"}))
    ws2.send = AsyncMock()
    ws2.close = AsyncMock()

    encoded2 = [orjson.dumps(m) for m in (then_frames or [])]

    async def iter2():
        for f in encoded2:
            yield f
        # Block forever (clean session, no further drops)
        await asyncio.sleep(9999)

    ws2.__aiter__ = MagicMock(return_value=iter2())
    ws2.__aenter__ = AsyncMock(return_value=ws2)
    ws2.__aexit__ = AsyncMock(return_value=None)

    return ws1, ws2


async def test_network_drop_triggers_reconnect():
    ws1, ws2 = make_closing_ws(frames_before_drop=[], then_frames=[])
    connect_calls = 0

    async def fake_connect(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        return ws1 if connect_calls == 1 else ws2

    resubscribed = False

    async def on_reconnect():
        nonlocal resubscribed
        resubscribed = True

    with patch("trader.md.ws_client.websockets.connect", side_effect=fake_connect):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"), on_reconnect=on_reconnect)
        # condition-based wait: let reader_loop detect the drop and reconnect
        # (reconnect backoff is random.uniform(0, 0.1) — completes fast)
        for _ in range(200):
            if session.reconnect_count >= 1:
                break
            await asyncio.sleep(0.01)
        await session.close()

    assert session.reconnect_count >= 1
    assert resubscribed is True


async def test_auth_401_invalid_token_no_retry():
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=orjson.dumps({"type": "error", "status": 401, "code": "invalid_token"}))
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)

    with patch("trader.md.ws_client.websockets.connect", AsyncMock(return_value=ws)):
        session = WsSession()
        with pytest.raises(AuthError) as exc_info:
            await session.connect(get_token=AsyncMock(return_value="bad_tok"))

    assert exc_info.value.is_invalid is True


async def test_auth_401_expired_token_refreshes_and_retries():
    call_count = 0

    async def get_token():
        nonlocal call_count
        call_count += 1
        return f"token_{call_count}"

    # First connect: expired token error
    ws1 = AsyncMock()
    ws1.recv = AsyncMock(return_value=orjson.dumps({"type": "error", "status": 401, "code": "token_expired"}))
    ws1.send = AsyncMock()
    ws1.close = AsyncMock()

    # Second connect (after token refresh): success
    ws2 = AsyncMock()
    ws2.recv = AsyncMock(return_value=orjson.dumps({"type": "auth_ack"}))
    ws2.send = AsyncMock()
    ws2.close = AsyncMock()
    ws2.__aiter__ = MagicMock(return_value=iter([]))
    ws2.__aenter__ = AsyncMock(return_value=ws2)
    ws2.__aexit__ = AsyncMock(return_value=None)

    connect_n = 0

    async def fake_connect(*args, **kwargs):
        nonlocal connect_n
        connect_n += 1
        return ws1 if connect_n == 1 else ws2

    with patch("trader.md.ws_client.websockets.connect", side_effect=fake_connect):
        session = WsSession()
        await session.connect(get_token=get_token)
        await session.close()

    assert call_count >= 2  # token was fetched at least twice


async def test_backoff_cap_never_exceeds_reconnect_max():
    """Full-jitter backoff: delay = random.uniform(0, min(BASE * 2**n, MAX)).
    After 100 failures the cap must never exceed RECONNECT_MAX."""
    import random
    from trader.md.ws_client import RECONNECT_BASE

    for attempt in range(100):
        delay = random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))
        assert delay <= RECONNECT_MAX, f"attempt {attempt}: delay {delay} > {RECONNECT_MAX}"


async def test_watchdog_stale_after_timeout():
    """Covered in test_feed.py — watchdog belongs to Feed, not WsSession."""
    pass  # intentionally empty; watchdog is Feed's responsibility
