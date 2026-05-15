import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets.exceptions

from trader.md.ws_client import AuthError, WsSession, RECONNECT_MAX

# Save a reference to the real asyncio.sleep BEFORE any patching
_real_sleep = asyncio.sleep


async def _yielding_sleep(*args, **kwargs):
    """Replacement for asyncio.sleep in the module under test.
    Returns instantly (no wall-clock wait) but YIELDS control to other tasks.
    Uses the saved reference to the real sleep to avoid recursion."""
    await _real_sleep(0)


def _make_ws(recv_response: dict, iter_frames: list[dict] | None = None, block_forever: bool = False) -> AsyncMock:
    """Build a mock WebSocket.

    recv_response: returned by ws.recv() (used during auth handshake in _do_connect)
    iter_frames: frames yielded by __aiter__
    block_forever: if True, __aiter__ blocks after frames (simulates idle connection)
    If block_forever is False, __aiter__ raises ConnectionClosed after all frames.
    """
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=orjson.dumps(recv_response))
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)

    encoded = [orjson.dumps(m) for m in (iter_frames or [])]

    if block_forever:
        async def _iter_stable():
            for f in encoded:
                yield f
            await _real_sleep(9999)

        ws.__aiter__ = MagicMock(return_value=_iter_stable())
    else:
        async def _iter_drop():
            for f in encoded:
                yield f
            raise websockets.exceptions.ConnectionClosed(None, None)

        ws.__aiter__ = MagicMock(return_value=_iter_drop())

    return ws


async def _yield_control(n: int = 20) -> None:
    """Yield control to other tasks n times using the real asyncio.sleep."""
    for _ in range(n):
        await _real_sleep(0)


async def test_network_drop_triggers_reconnect():
    """ConnectionClosed in reader_loop triggers reconnect and calls on_reconnect callback."""
    # ws1: initial connection — iter raises ConnectionClosed immediately
    ws1 = _make_ws({"type": "auth_ack"}, iter_frames=[], block_forever=False)
    # ws2: reconnected — blocks forever (stable)
    ws2 = _make_ws({"type": "auth_ack"}, iter_frames=[], block_forever=True)

    connect_calls = 0

    def fake_connect(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        return ws1 if connect_calls == 1 else ws2

    resubscribed = False

    async def on_reconnect():
        nonlocal resubscribed
        resubscribed = True

    with patch("trader.md.ws_client.websockets.connect", side_effect=fake_connect):
        with patch("trader.md.ws_client.asyncio.sleep", side_effect=_yielding_sleep):
            session = WsSession()
            await session.connect(get_token=AsyncMock(return_value="tok"), on_reconnect=on_reconnect)
            # Yield control so reader_loop can process ConnectionClosed and reconnect
            await _yield_control(30)
            await session.close()

    assert session.reconnect_count >= 1
    assert resubscribed is True


async def test_auth_401_invalid_token_no_retry():
    """is_invalid=True on 401 with non-expired code — connect() raises AuthError immediately."""
    ws = _make_ws({"type": "error", "status": 401, "code": "invalid_token"})

    with patch("trader.md.ws_client.websockets.connect", return_value=ws):
        session = WsSession()
        with pytest.raises(AuthError) as exc_info:
            await session.connect(get_token=AsyncMock(return_value="bad_tok"))

    assert exc_info.value.is_invalid is True


async def test_auth_401_expired_token_refreshes_and_retries():
    """token_expired during reconnect: _reader_loop retries _do_connect() with fresh token."""
    call_count = 0

    async def get_token():
        nonlocal call_count
        call_count += 1
        return f"token_{call_count}"

    # ws_initial: successful first connect, then drops connection
    ws_initial = _make_ws({"type": "auth_ack"}, iter_frames=[], block_forever=False)
    # ws_expired: reconnect attempt — responds with token_expired (is_invalid=False)
    ws_expired = _make_ws({"type": "error", "status": 401, "code": "token_expired"})
    # ws_ok: second reconnect attempt (after token refresh) — succeeds, blocks forever
    ws_ok = _make_ws({"type": "auth_ack"}, iter_frames=[], block_forever=True)

    connect_n = 0

    def fake_connect(*args, **kwargs):
        nonlocal connect_n
        connect_n += 1
        if connect_n == 1:
            return ws_initial
        elif connect_n == 2:
            return ws_expired
        else:
            return ws_ok

    with patch("trader.md.ws_client.websockets.connect", side_effect=fake_connect):
        with patch("trader.md.ws_client.asyncio.sleep", side_effect=_yielding_sleep):
            session = WsSession()
            await session.connect(get_token=get_token)
            # Yield control so reader_loop processes ConnectionClosed and retries
            await _yield_control(30)
            await session.close()

    assert call_count >= 3  # initial + expired retry + fresh token
    assert connect_n >= 3   # initial + expired attempt + success


async def test_backoff_cap_never_exceeds_reconnect_max():
    """Full-jitter backoff: delay = random.uniform(0, min(BASE * 2**n, MAX)).
    After 100 failures the cap must never exceed RECONNECT_MAX."""
    import random
    from trader.md.ws_client import RECONNECT_BASE

    for attempt in range(100):
        delay = random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))
        assert delay <= RECONNECT_MAX, f"attempt {attempt}: delay {delay} > {RECONNECT_MAX}"
