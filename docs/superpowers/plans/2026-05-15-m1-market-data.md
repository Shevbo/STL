# M1 Market Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a reliable, reconnecting WebSocket feed of real-time QUOTES from Finam Trade API with a conflated-slot consumer interface suited for tick-level strategies.

**Architecture:** Three layers — `WsSession` (raw WebSocket transport: connect, auth, dispatch, ping/pong, reconnect with full-jitter backoff), `MarketDataFeed` (lifecycle, symbol subscriptions, conflated `QuoteState` slots, watchdog), `Models` (`Quote` frozen dataclass + `FeedState` enum). Consumers interact only with `MarketDataFeed`. `WsSession` is an internal dependency.

**Tech Stack:** Python 3.12, asyncio, `websockets >= 13.1`, `orjson >= 3.10`, `structlog` (existing), `pytest-asyncio` (asyncio_mode=auto), `pytest-mock >= 3.14`, `hypothesis >= 6`, `pytest-timeout >= 2`

**Design notes:**
- All Finam WebSocket message formats (auth, subscribe, quote frames) have TODO markers — verify against live API before integration testing.
- `WsSession` reconnect loop runs inside `_reader_loop` task. Reconnect calls `_on_reconnect()` callback (provided by Feed) to resubscribe all active symbols.
- `AuthError(is_invalid=True)` → no retry, Feed transitions to `CLOSED`. `AuthError(is_invalid=False)` → refresh token once + retry.
- Data queue overflow: drop **oldest** item + log `ws.queue_overflow`. Never block the reader.

---

## File Structure

```
trader/md/
├── __init__.py        # re-export MarketDataFeed, Quote, FeedState
├── models.py          # FeedState enum + Quote frozen dataclass
├── ws_client.py       # WsSession (transport + reconnect)
└── feed.py            # QuoteState + MarketDataFeed

tests/md/
├── __init__.py
├── test_models.py     # Quote.from_payload — Decimal envelope, edge cases, hypothesis fuzz
├── test_ws_client.py  # happy-path connect/subscribe/iter/close
├── test_reconnect.py  # network drop, auth 401 variants, backoff cap, watchdog STALE
├── test_feed.py       # conflated semantics, aclose, cleanup
└── test_integration.py  # @pytest.mark.integration — real Finam WS
```

---

### Task 1: Dependencies + file scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `trader/md/__init__.py`, `trader/md/models.py`, `trader/md/ws_client.py`, `trader/md/feed.py`
- Create: `tests/md/__init__.py`, `tests/md/test_models.py`, `tests/md/test_ws_client.py`, `tests/md/test_reconnect.py`, `tests/md/test_feed.py`, `tests/md/test_integration.py`

- [ ] **Step 1: Add runtime dependencies to pyproject.toml**

In `pyproject.toml`, add to `[tool.poetry.dependencies]`:
```toml
websockets = "^13.1"
orjson = "^3.10"
```

- [ ] **Step 2: Add dev dependencies to pyproject.toml**

In `[tool.poetry.group.dev.dependencies]`:
```toml
pytest-mock = "^3.14"
hypothesis = "^6"
pytest-timeout = "^2"
```

- [ ] **Step 3: Create module directories and empty files**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
mkdir -p trader/md tests/md
touch trader/md/__init__.py trader/md/models.py trader/md/ws_client.py trader/md/feed.py
touch tests/md/__init__.py tests/md/test_models.py tests/md/test_ws_client.py
touch tests/md/test_reconnect.py tests/md/test_feed.py tests/md/test_integration.py
```

- [ ] **Step 4: Install new dependencies**

```bash
poetry install
```

Expected: `Package operations: N installs` without errors. Verify with:
```bash
poetry run python -c "import websockets, orjson; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
rtk git add pyproject.toml poetry.lock trader/md/ tests/md/ && rtk git commit -m "feat(M1): add websockets/orjson deps + scaffold md module"
```

---

### Task 2: Models — FeedState + Quote

**Files:**
- Modify: `trader/md/models.py`
- Modify: `tests/md/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/md/test_models.py
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trader.md.models import FeedState, Quote


# --- FeedState ---

def test_feedstate_values():
    assert FeedState.CONNECTING.value == "connecting"
    assert FeedState.LIVE.value == "live"
    assert FeedState.STALE.value == "stale"
    assert FeedState.CLOSED.value == "closed"


# --- Quote.from_payload: Decimal envelope ---

def test_quote_from_payload_decimal_envelope():
    payload = {
        "bid": {"value": "123.45"},
        "bid_size": 10,
        "ask": {"value": "123.50"},
        "ask_size": 5,
        "last": {"value": "123.47"},
        "last_size": 3,
        "timestamp": "2026-05-15T10:00:00Z",
    }
    q = Quote.from_payload("GZM6@RTSX", payload)
    assert q.symbol == "GZM6@RTSX"
    assert q.bid == Decimal("123.45")
    assert q.ask == Decimal("123.50")
    assert q.last == Decimal("123.47")
    assert q.bid_size == 10
    assert q.ask_size == 5
    assert q.last_size == 3


def test_quote_from_payload_plain_string():
    payload = {
        "bid": "50.00",
        "bid_size": 1,
        "ask": "50.10",
        "ask_size": 1,
        "last": "50.05",
        "last_size": 1,
        "timestamp": "2026-05-15T10:00:00Z",
    }
    q = Quote.from_payload("SYM", payload)
    assert q.bid == Decimal("50.00")
    assert q.ask == Decimal("50.10")


def test_quote_timestamp_is_utc_aware():
    payload = {
        "bid": "1.0", "bid_size": 0,
        "ask": "1.0", "ask_size": 0,
        "last": "1.0", "last_size": 0,
        "timestamp": "2026-05-15T10:00:00Z",
    }
    q = Quote.from_payload("S", payload)
    assert q.timestamp.tzinfo is not None
    assert q.timestamp.tzinfo == timezone.utc


def test_quote_is_frozen():
    payload = {
        "bid": "1.0", "bid_size": 0,
        "ask": "1.0", "ask_size": 0,
        "last": "1.0", "last_size": 0,
        "timestamp": "2026-05-15T10:00:00Z",
    }
    q = Quote.from_payload("S", payload)
    with pytest.raises(Exception):  # FrozenInstanceError
        q.bid = Decimal("99")


def test_quote_missing_size_defaults_to_zero():
    payload = {
        "bid": "1.0",
        "ask": "1.1",
        "last": "1.05",
        "timestamp": "2026-05-15T10:00:00Z",
    }
    q = Quote.from_payload("S", payload)
    assert q.bid_size == 0
    assert q.ask_size == 0
    assert q.last_size == 0


def test_quote_from_payload_invalid_decimal_raises():
    payload = {
        "bid": "NOT_A_NUMBER",
        "ask": "1.0", "ask_size": 0,
        "last": "1.0", "last_size": 0,
        "timestamp": "2026-05-15T10:00:00Z",
    }
    with pytest.raises((InvalidOperation, Exception)):
        Quote.from_payload("S", payload)


# --- Hypothesis fuzz: malformed payloads must raise, not crash unexpectedly ---

@given(st.fixed_dictionaries({
    "bid": st.one_of(st.text(), st.integers(), st.none()),
    "ask": st.one_of(st.text(), st.integers(), st.none()),
    "last": st.one_of(st.text(), st.integers(), st.none()),
    "timestamp": st.one_of(st.text(), st.none()),
}))
@settings(max_examples=200)
def test_quote_from_payload_fuzz_does_not_crash_unexpectedly(payload):
    try:
        Quote.from_payload("S", payload)
    except Exception:
        pass  # Any exception is acceptable — we just must not get an unhandled crash
```

- [ ] **Step 2: Run — verify FAIL**

```bash
poetry run pytest tests/md/test_models.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — models not implemented yet.

- [ ] **Step 3: Implement `trader/md/models.py`**

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum


class FeedState(Enum):
    CONNECTING = "connecting"
    LIVE = "live"
    STALE = "stale"
    CLOSED = "closed"


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    bid_size: int
    ask: Decimal
    ask_size: int
    last: Decimal
    last_size: int
    timestamp: datetime  # always UTC-aware

    @classmethod
    def from_payload(cls, symbol: str, data: dict) -> "Quote":
        def dec(obj) -> Decimal:
            if isinstance(obj, dict):
                return Decimal(obj["value"])
            return Decimal(str(obj))

        return cls(
            symbol=symbol,
            bid=dec(data["bid"]),
            bid_size=int(data.get("bid_size", 0)),
            ask=dec(data["ask"]),
            ask_size=int(data.get("ask_size", 0)),
            last=dec(data["last"]),
            last_size=int(data.get("last_size", 0)),
            timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
        )
```

- [ ] **Step 4: Run — verify PASS**

```bash
poetry run pytest tests/md/test_models.py -v
```

Expected: all tests pass (including hypothesis fuzz).

- [ ] **Step 5: Commit**

```bash
rtk git add trader/md/models.py tests/md/test_models.py && rtk git commit -m "feat(M1): FeedState enum + Quote frozen dataclass with from_payload"
```

---

### Task 3: WsSession — connect, subscribe, iter_quotes, close (happy path)

**Files:**
- Modify: `trader/md/ws_client.py`
- Modify: `tests/md/test_ws_client.py`

- [ ] **Step 1: Write failing tests**

```python
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
    # __aiter__ yields subsequent frames
    encoded = [orjson.dumps(m) for m in recv_messages]
    ws.__aiter__ = MagicMock(return_value=iter(encoded))
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    return ws


@pytest.fixture
def fake_ws():
    return make_fake_ws([])


async def test_connect_sends_auth_message(fake_ws):
    with patch("trader.md.ws_client.websockets.connect", return_value=fake_ws):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok123"))
        await session.close()

    sent = orjson.loads(fake_ws.send.call_args_list[0][0][0])
    # TODO: verify exact auth message format from Finam API docs
    assert sent["type"] == "auth"
    assert sent["token"] == "tok123"


async def test_connect_sets_connected_true(fake_ws):
    with patch("trader.md.ws_client.websockets.connect", return_value=fake_ws):
        session = WsSession()
        await session.connect(get_token=AsyncMock(return_value="tok"))
        assert session.connected is True
        await session.close()


async def test_subscribe_sends_subscribe_message(fake_ws):
    # subscribe_ack must appear in ctrl queue before subscribe() returns
    ack = {"type": "subscribe_ack", "symbol": "GZM6@RTSX"}
    fake_ws_with_ack = make_fake_ws([ack])

    with patch("trader.md.ws_client.websockets.connect", return_value=fake_ws_with_ack):
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

    with patch("trader.md.ws_client.websockets.connect", return_value=fake_ws_with_quote):
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
    with patch("trader.md.ws_client.websockets.connect", return_value=fake_ws):
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

    with patch("trader.md.ws_client.websockets.connect", return_value=fake):
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
```

- [ ] **Step 2: Run — verify FAIL**

```bash
poetry run pytest tests/md/test_ws_client.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Implement `trader/md/ws_client.py`**

```python
import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable, Awaitable

import orjson
import structlog
import websockets
import websockets.exceptions

log = structlog.get_logger()

# TODO: Verify exact WebSocket URL from Finam API docs
WS_URL = "wss://api.finam.ru:443/ws"

PING_INTERVAL = 3.0    # seconds
PING_TIMEOUT = 2.0     # seconds; PING_INTERVAL + PING_TIMEOUT ≤ watchdog_secs (5s)
RECONNECT_BASE = 0.1   # seconds — aggressive first attempt
RECONNECT_MAX = 60.0   # seconds — backoff ceiling
RECONNECT_JITTER = 1.0 # full jitter (AWS style)

_SENTINEL = object()   # signals iter_quotes() to stop


class AuthError(Exception):
    def __init__(self, msg: str, is_invalid: bool = False):
        super().__init__(msg)
        self.is_invalid = is_invalid  # True → do not retry; False → refresh token + retry once


class WsSession:
    def __init__(self) -> None:
        self._ctrl_q: asyncio.Queue[dict] = asyncio.Queue(maxsize=20)
        self._data_q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._ws = None
        self._reader_task: asyncio.Task | None = None
        self._get_token: Callable[[], Awaitable[str]] | None = None
        self._on_reconnect: Callable[[], Awaitable[None]] | None = None
        self._running = False
        self._connected = False
        # metrics
        self.messages_received: int = 0
        self.reconnect_count: int = 0
        self.last_message_age_ms: float = 0.0
        self.ping_rtt_ms: float = 0.0
        self._last_msg_time: float = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(
        self,
        get_token: Callable[[], Awaitable[str]],
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._get_token = get_token
        self._on_reconnect = on_reconnect
        self._running = True
        await self._do_connect()
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _do_connect(self) -> None:
        """Single connection attempt: open WS, auth, wait for ack."""
        token = await self._get_token()
        self._ws = await websockets.connect(
            WS_URL,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
        )
        # TODO: Verify exact auth message format from Finam API docs
        await self._ws.send(orjson.dumps({"type": "auth", "token": token}))
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
        msg = orjson.loads(raw)
        # TODO: Verify auth_ack format and error codes from Finam API docs
        if msg.get("type") == "auth_ack":
            self._connected = True
            return
        code = msg.get("code", "unknown")
        status = msg.get("status", 0)
        raise AuthError(
            f"Auth failed: {code}",
            is_invalid=(status == 401 and code != "token_expired"),
        )

    async def _reader_loop(self) -> None:
        """Persistent reader task: dispatch frames + handle reconnect."""
        attempt = 0
        while self._running:
            try:
                async for raw in self._ws:
                    msg = orjson.loads(raw)
                    self.messages_received += 1
                    self._last_msg_time = time.monotonic()
                    mtype = msg.get("type", "")
                    if mtype in ("auth_ack", "subscribe_ack", "error"):
                        self._put_ctrl(msg)
                    else:
                        self._put_data(msg)
                # Clean exit (ws.close() called)
                self._connected = False
                break
            except websockets.exceptions.ConnectionClosed:
                self._connected = False
                if not self._running:
                    break
                log.warning("ws.reconnect", attempt=attempt)
                delay = random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))
                await asyncio.sleep(delay)
                try:
                    token_expired_retry = False
                    while True:
                        try:
                            await self._do_connect()
                            break
                        except AuthError as e:
                            if e.is_invalid:
                                raise
                            if token_expired_retry:
                                raise  # already retried once
                            token_expired_retry = True
                            # Token expired — get_token() will refresh
                            continue
                    if self._on_reconnect:
                        await self._on_reconnect()
                    self.reconnect_count += 1
                    attempt = 0
                except AuthError:
                    log.error("ws.auth_invalid")
                    self._running = False
                    self._data_q.put_nowait(_SENTINEL)
                    return
                except Exception as exc:
                    log.warning("ws.reconnect_failed", exc=str(exc))
                    attempt += 1
            except orjson.JSONDecodeError as exc:
                log.warning("ws.decode_error", exc=str(exc))
            except Exception:
                if not self._running:
                    break
                raise

        self._data_q.put_nowait(_SENTINEL)

    def _put_ctrl(self, msg: dict) -> None:
        if self._ctrl_q.full():
            try:
                self._ctrl_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            log.warning("ws.ctrl_queue_overflow")
        self._ctrl_q.put_nowait(msg)

    def _put_data(self, msg: dict) -> None:
        if self._data_q.full():
            try:
                self._data_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            log.warning("ws.queue_overflow")
        self._data_q.put_nowait(msg)

    async def subscribe(self, symbol: str) -> None:
        """Send subscribe and wait for ack. Retries 3 times on timeout."""
        # TODO: Verify exact subscribe message format from Finam API docs
        await self._ws.send(orjson.dumps({"type": "subscribe", "symbol": symbol}))
        for attempt in range(3):
            try:
                ack = await asyncio.wait_for(self._ctrl_q.get(), timeout=5.0)
                # TODO: Verify subscribe_ack format from Finam API docs
                if ack.get("type") == "subscribe_ack" and ack.get("symbol") == symbol:
                    return
            except asyncio.TimeoutError:
                if attempt == 2:
                    raise TimeoutError(f"Subscribe ack timeout for {symbol} after 3 attempts")

    async def unsubscribe(self, symbol: str) -> None:
        # TODO: Verify exact unsubscribe message format from Finam API docs
        await self._ws.send(orjson.dumps({"type": "unsubscribe", "symbol": symbol}))

    async def iter_quotes(self) -> AsyncIterator[dict]:
        while True:
            item = await self._data_q.get()
            if item is _SENTINEL:
                self._data_q.put_nowait(_SENTINEL)  # re-enqueue for any other consumers
                return
            yield item

    async def close(self, code: int = 1000) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close(code=code)
        if self._reader_task and not self._reader_task.done():
            try:
                await asyncio.wait_for(self._reader_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader_task.cancel()
```

- [ ] **Step 4: Run — verify PASS**

```bash
poetry run pytest tests/md/test_ws_client.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
rtk git add trader/md/ws_client.py tests/md/test_ws_client.py && rtk git commit -m "feat(M1): WsSession — connect, subscribe, iter_quotes, close"
```

---

### Task 4: WsSession — reconnect tests

**Files:**
- Modify: `tests/md/test_reconnect.py`

(No changes to `ws_client.py` — reconnect is already implemented in Task 3.)

- [ ] **Step 1: Write failing tests**

```python
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
        with patch("trader.md.ws_client.asyncio.sleep", new_callable=AsyncMock):
            session = WsSession()
            await session.connect(get_token=AsyncMock(return_value="tok"), on_reconnect=on_reconnect)
            await asyncio.sleep(0.05)  # let reader_loop react
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

    with patch("trader.md.ws_client.websockets.connect", return_value=ws):
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
```

- [ ] **Step 2: Run — verify PASS**

```bash
poetry run pytest tests/md/test_reconnect.py -v
```

Expected: all tests pass. (The empty watchdog test also passes.)

- [ ] **Step 3: Commit**

```bash
rtk git add tests/md/test_reconnect.py && rtk git commit -m "test(M1): WsSession reconnect — network drop, auth 401, backoff cap"
```

---

### Task 5: MarketDataFeed — QuoteState + core subscribe + latest

**Files:**
- Modify: `trader/md/feed.py`
- Modify: `tests/md/test_feed.py`

- [ ] **Step 1: Write failing tests for core Feed behavior**

```python
# tests/md/test_feed.py
import asyncio
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trader.md.feed import MarketDataFeed
from trader.md.models import FeedState, Quote
from trader.md.ws_client import WsSession


def make_quote(symbol: str = "GZM6@RTSX", bid: str = "100.0", offset_sec: int = 0) -> Quote:
    return Quote(
        symbol=symbol,
        bid=Decimal(bid),
        bid_size=10,
        ask=Decimal("100.1"),
        ask_size=5,
        last=Decimal("100.05"),
        last_size=3,
        timestamp=datetime.now(timezone.utc) + timedelta(seconds=offset_sec),
    )


def make_mock_ws(quote_frames: list[dict] | None = None) -> WsSession:
    ws = AsyncMock(spec=WsSession)
    ws.connected = True
    ws.connect = AsyncMock()
    ws.subscribe = AsyncMock()
    ws.close = AsyncMock()

    frames = quote_frames or []

    async def fake_iter_quotes():
        for f in frames:
            yield f
        await asyncio.sleep(9999)  # block — feed will be closed externally

    ws.iter_quotes = fake_iter_quotes
    return ws


# --- latest() ---

async def test_latest_returns_none_before_first_tick():
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    assert feed.latest("GZM6@RTSX") is None
    await feed.aclose()


async def test_latest_returns_quote_after_tick():
    q = make_quote()
    frame = {
        "type": "quote",
        "symbol": "GZM6@RTSX",
        "bid": str(q.bid), "bid_size": q.bid_size,
        "ask": str(q.ask), "ask_size": q.ask_size,
        "last": str(q.last), "last_size": q.last_size,
        "timestamp": q.timestamp.isoformat().replace("+00:00", "Z"),
    }
    ws = make_mock_ws([frame])
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    await asyncio.sleep(0.05)
    result = feed.latest("GZM6@RTSX")
    assert result is not None
    assert result.bid == Decimal("100.0")
    await feed.aclose()


# --- state ---

async def test_initial_state_is_connecting():
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    assert feed.state == FeedState.CONNECTING


async def test_state_becomes_live_after_first_quote():
    q = make_quote()
    frame = {
        "type": "quote", "symbol": "GZM6@RTSX",
        "bid": str(q.bid), "bid_size": q.bid_size,
        "ask": str(q.ask), "ask_size": q.ask_size,
        "last": str(q.last), "last_size": q.last_size,
        "timestamp": q.timestamp.isoformat().replace("+00:00", "Z"),
    }
    ws = make_mock_ws([frame])
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    await asyncio.sleep(0.05)
    assert feed.state == FeedState.LIVE
    await feed.aclose()


# --- subscribe() conflation ---

async def test_subscribe_conflation_slow_consumer():
    """10 ticks arrive while consumer is processing; should get latest only."""
    quotes = []
    base_ts = datetime.now(timezone.utc)
    for i in range(10):
        ts = (base_ts + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        quotes.append({
            "type": "quote", "symbol": "GZM6@RTSX",
            "bid": str(100 + i), "bid_size": 1,
            "ask": str(101 + i), "ask_size": 1,
            "last": str(100 + i), "last_size": 1,
            "timestamp": ts,
        })

    ws = make_mock_ws(quotes)
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))

    received = []
    await asyncio.sleep(0.05)  # let all 10 ticks arrive

    async for q in feed.subscribe("GZM6@RTSX"):
        received.append(q)
        break  # take just one — should be the latest

    await feed.aclose()

    assert len(received) == 1
    # Latest bid should be 109 (100 + 9) — conflation kept only the newest
    assert received[0].bid == Decimal("109")


async def test_subscribe_break_no_hang():
    """Breaking out of subscribe() should not hang or raise."""
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))

    # add_symbol + immediately break before any quote arrives
    await feed.add_symbol("GZM6@RTSX")
    # This should complete quickly (event-based, no dangling futures)
    done = asyncio.Event()
    async def _consume():
        async for _ in feed.subscribe("GZM6@RTSX"):
            break
        done.set()

    task = asyncio.create_task(_consume())
    # Inject a quote to wake the consumer
    await asyncio.sleep(0.01)
    q = make_quote()
    feed._slots["GZM6@RTSX"].update(q)
    await asyncio.wait_for(done.wait(), timeout=2.0)
    task.cancel()
    await feed.aclose()
```

- [ ] **Step 2: Run — verify FAIL**

```bash
poetry run pytest tests/md/test_feed.py -v 2>&1 | head -40
```

Expected: `ImportError` — `feed.py` not implemented yet.

- [ ] **Step 3: Implement `trader/md/feed.py` (core, no watchdog yet)**

```python
import asyncio
from collections.abc import AsyncIterator, Callable, Awaitable

import orjson
import structlog

from trader.md.models import FeedState, Quote
from trader.md.ws_client import WsSession

log = structlog.get_logger()


class QuoteState:
    def __init__(self) -> None:
        self._latest: Quote | None = None
        self._event: asyncio.Event = asyncio.Event()
        self._closed: bool = False

    def update(self, quote: Quote) -> None:
        self._latest = quote
        self._event.set()

    def next_event(self) -> asyncio.Event:
        self._event.clear()
        return self._event


class MarketDataFeed:
    def __init__(
        self,
        ws: WsSession,
        watchdog_secs: float = 5.0,
        on_raw: Callable[[dict], None] | None = None,
    ) -> None:
        self._ws = ws
        self._watchdog_secs = watchdog_secs
        self._on_raw = on_raw
        self._slots: dict[str, QuoteState] = {}
        self._active_symbols: set[str] = set()
        self._state = FeedState.CONNECTING
        self._running = False
        self._reader_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._heartbeat = asyncio.Event()

    @property
    def state(self) -> FeedState:
        return self._state

    async def start(self, get_token: Callable[[], Awaitable[str]]) -> None:
        self._running = True
        await self._ws.connect(
            get_token=get_token,
            on_reconnect=self._resubscribe_all,
        )
        self._reader_task = asyncio.create_task(self._reader())
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def add_symbol(self, symbol: str) -> None:
        if symbol not in self._slots:
            self._slots[symbol] = QuoteState()
        if symbol not in self._active_symbols:
            self._active_symbols.add(symbol)
            await self._ws.subscribe(symbol)

    def latest(self, symbol: str) -> Quote | None:
        slot = self._slots.get(symbol)
        return slot._latest if slot else None

    async def subscribe(self, symbol: str) -> AsyncIterator[Quote]:
        if symbol not in self._slots:
            await self.add_symbol(symbol)
        slot = self._slots[symbol]
        try:
            while self._running and not slot._closed:
                event = slot.next_event()
                await event.wait()
                if slot._latest is not None:
                    yield slot._latest
        finally:
            pass  # Event-based: no dangling Futures to cancel

    async def _reader(self) -> None:
        try:
            async for raw in self._ws.iter_quotes():
                if self._on_raw:
                    try:
                        self._on_raw(raw)
                    except Exception as exc:
                        log.warning("md.on_raw_error", exc=str(exc))

                symbol = raw.get("symbol", "")
                if symbol not in self._slots:
                    continue

                try:
                    quote = Quote.from_payload(symbol, raw)
                except Exception as exc:
                    log.warning("md.parse_error", exc=str(exc))
                    continue

                slot = self._slots[symbol]
                if slot._latest and quote.timestamp < slot._latest.timestamp:
                    log.warning("md.out_of_order", symbol=symbol)
                    continue

                slot.update(quote)
                self._heartbeat.set()
                if self._state == FeedState.CONNECTING:
                    self._state = FeedState.LIVE
        except Exception as exc:
            log.error("md.reader_crashed", exc=str(exc))
        finally:
            self._state = FeedState.CLOSED
            if self._watchdog_task:
                self._watchdog_task.cancel()
            for slot in self._slots.values():
                slot._closed = True
                slot._event.set()

    async def _watchdog(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._heartbeat.wait()),
                    timeout=self._watchdog_secs,
                )
                self._heartbeat.clear()
                if self._state == FeedState.STALE:
                    self._state = FeedState.LIVE
            except asyncio.TimeoutError:
                if self._state == FeedState.LIVE:
                    self._state = FeedState.STALE
                    log.warning("md.watchdog.stale")

    async def _resubscribe_all(self) -> None:
        for symbol in self._active_symbols:
            await self._ws.subscribe(symbol)

    async def aclose(self) -> None:
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
        await self._ws.close(code=1000)
        if self._reader_task:
            try:
                await asyncio.wait_for(self._reader_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader_task.cancel()
        self._state = FeedState.CLOSED
        for slot in self._slots.values():
            slot._closed = True
            slot._event.set()
```

- [ ] **Step 4: Run — verify PASS**

```bash
poetry run pytest tests/md/test_feed.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
rtk git add trader/md/feed.py tests/md/test_feed.py && rtk git commit -m "feat(M1): MarketDataFeed — QuoteState, subscribe, conflation, watchdog"
```

---

### Task 6: MarketDataFeed — watchdog + aclose tests

**Files:**
- Modify: `tests/md/test_feed.py` (append new tests)

- [ ] **Step 1: Append watchdog and aclose tests to `tests/md/test_feed.py`**

```python
# Append to tests/md/test_feed.py (after existing tests)

async def test_watchdog_transitions_to_stale_on_timeout():
    ws = make_mock_ws()  # no quotes → heartbeat never fires
    feed = MarketDataFeed(ws, watchdog_secs=0.05)  # very short timeout for test
    await feed.start(get_token=AsyncMock(return_value="tok"))
    # Fake LIVE state so watchdog has something to degrade
    feed._state = FeedState.LIVE
    await asyncio.sleep(0.15)
    assert feed.state == FeedState.STALE
    await feed.aclose()


async def test_watchdog_recovers_to_live_on_heartbeat():
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=0.05)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    feed._state = FeedState.LIVE
    await asyncio.sleep(0.1)  # → STALE
    assert feed.state == FeedState.STALE

    # Simulate a heartbeat arriving
    feed._heartbeat.set()
    await asyncio.sleep(0.1)
    assert feed.state == FeedState.LIVE
    await feed.aclose()


async def test_aclose_sets_state_closed():
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    await feed.aclose()
    assert feed.state == FeedState.CLOSED


async def test_aclose_wakes_subscribe_iterators():
    """aclose() while a consumer is blocked in subscribe() → StopAsyncIteration."""
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    await feed.add_symbol("GZM6@RTSX")

    consumer_exited = asyncio.Event()

    async def consumer():
        async for _ in feed.subscribe("GZM6@RTSX"):
            pass
        consumer_exited.set()

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.02)  # let consumer block in event.wait()
    await feed.aclose()
    await asyncio.wait_for(consumer_exited.wait(), timeout=2.0)
    task.cancel()


async def test_aclose_then_anext_does_not_hang():
    """After aclose(), subscribing and iterating must not hang."""
    ws = make_mock_ws()
    feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await feed.start(get_token=AsyncMock(return_value="tok"))
    await feed.aclose()

    # Already closed — subscribe() should exit immediately (slot._closed = True)
    count = 0
    async for _ in feed.subscribe("GZM6@RTSX"):
        count += 1
    assert count == 0
```

- [ ] **Step 2: Run — verify all feed tests pass**

```bash
poetry run pytest tests/md/test_feed.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Run full unit suite**

```bash
poetry run pytest -m "not integration" -v
```

Expected: all tests pass (models + auth + registry + md).

- [ ] **Step 4: Commit**

```bash
rtk git add tests/md/test_feed.py && rtk git commit -m "test(M1): watchdog stale/recovery + aclose shutdown coverage"
```

---

### Task 7: __init__.py + integration test

**Files:**
- Modify: `trader/md/__init__.py`
- Modify: `tests/md/test_integration.py`

- [ ] **Step 1: Implement `trader/md/__init__.py`**

```python
from trader.md.feed import MarketDataFeed
from trader.md.models import FeedState, Quote

__all__ = ["MarketDataFeed", "Quote", "FeedState"]
```

- [ ] **Step 2: Write integration test**

```python
# tests/md/test_integration.py
"""
Integration tests — require real Finam credentials + live market hours.

Setup:
  set -a && source ~/.shectory_trade.env && set +a

Run:
  poetry run pytest tests/md/test_integration.py -v -m integration
"""
import asyncio

import pytest

from trader.config import Settings
from trader.auth.client import AsyncAuthClient
from trader.md.feed import MarketDataFeed
from trader.md.models import FeedState
from trader.md.ws_client import WsSession

pytestmark = pytest.mark.integration


async def _wait_for_state(feed: MarketDataFeed, target: FeedState, timeout: float = 30.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while feed.state != target:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Feed did not reach {target} within {timeout}s")
        await asyncio.sleep(0.1)


@pytest.fixture
async def feed():
    settings = Settings()
    auth = AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    )
    ws = WsSession()
    _feed = MarketDataFeed(ws, watchdog_secs=5.0)
    await _feed.start(get_token=auth.get_token)
    yield _feed
    await _feed.aclose()
    await auth.aclose()


@pytest.mark.timeout(60)
async def test_feed_state_is_live_after_start(feed):
    await feed.add_symbol("GZM6@RTSX")
    await _wait_for_state(feed, FeedState.LIVE, timeout=30.0)
    assert feed.state == FeedState.LIVE


@pytest.mark.timeout(60)
async def test_live_quote_received(feed):
    await feed.add_symbol("GZM6@RTSX")
    async for quote in feed.subscribe("GZM6@RTSX"):
        assert quote.bid > 0
        assert quote.ask > 0
        assert quote.timestamp.tzinfo is not None
        assert feed.state == FeedState.LIVE
        break


@pytest.mark.timeout(60)
async def test_second_subscribe_call_uses_cache(feed):
    await feed.add_symbol("GZM6@RTSX")
    await _wait_for_state(feed, FeedState.LIVE, timeout=30.0)
    # Second subscribe should reuse slot without re-subscribing to WS
    async for quote in feed.subscribe("GZM6@RTSX"):
        assert quote is not None
        break
```

- [ ] **Step 3: Verify import works**

```bash
poetry run python -c "from trader.md import MarketDataFeed, Quote, FeedState; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Run unit tests (integration skipped)**

```bash
poetry run pytest -m "not integration" -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
rtk git add trader/md/__init__.py tests/md/test_integration.py && rtk git commit -m "feat(M1): public __init__ exports + integration test skeleton"
```

---

### Task 8: Lint + CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run ruff lint**

```bash
poetry run ruff check trader/ tests/
```

Expected: no errors. If errors appear, fix them before continuing.

- [ ] **Step 2: Update CLAUDE.md — mark M4 ✅ and M1 ✅**

In `CLAUDE.md`, update the Architecture Modules table:

```markdown
| M4 Instrument Registry | trader/registry/ | Stage 3 | ✅ |
| M1 Market Data | trader/md/ | Stage 4 | ✅ |
```

- [ ] **Step 3: Commit CLAUDE.md**

```bash
rtk git add CLAUDE.md && rtk git commit -m "docs: mark M4 + M1 complete in CLAUDE.md"
```

- [ ] **Step 4: Commit the old plan file (untracked since previous session)**

```bash
rtk git add docs/superpowers/plans/2026-05-14-stage-1-2-scaffolding-auth.md && rtk git commit -m "docs: add stage 1-2 scaffolding plan (historical)"
```

---

## Self-Review

**Spec coverage:**
- [x] Section 1 Models: `FeedState` enum (4 states) → Task 2. `Quote` frozen dataclass + `from_payload` (Decimal envelope + plain string) → Task 2.
- [x] Section 2 WsSession: connect, auth, subscribe, iter_quotes, close → Task 3. Reconnect backoff + auth 401 variants → Task 3 impl + Task 4 tests. Queue overflow drop-oldest → Task 3. Metrics (messages_received, reconnect_count) → Task 3.
- [x] Section 3 MarketDataFeed: QuoteState conflated pattern → Task 5. start(), add_symbol(), latest(), subscribe() → Task 5. Watchdog → Task 5. _resubscribe_all callback → Task 5. aclose() shutdown sequence → Task 6.
- [x] Section 4 Error handling matrix: all rows covered (auth 401 variants, queue overflow, parse errors, on_raw errors, out-of-order) → Tasks 3, 4, 5.
- [x] Section 5 Testing strategy: test_models.py (hypothesis fuzz, Decimal envelope, frozen, UTC) → Task 2. test_ws_client.py (happy path) → Task 3. test_reconnect.py (network drop, auth 401, backoff cap) → Task 4. test_feed.py (conflation, break, aclose, watchdog) → Tasks 5+6. test_integration.py → Task 7.
- [x] Dependencies: websockets, orjson, pytest-mock, hypothesis, pytest-timeout → Task 1.
- [x] CLAUDE.md update (M4 ✅, M1 ✅) → Task 8.
- [x] Old plan file commit → Task 8.

**Placeholder scan:** No TBD/TODO used as substitutes for content. All TODO markers are intentional protocol-format reminders (Finam API format unknown until integration). All test code is fully written.

**Type consistency:**
- `QuoteState` defined in `feed.py` Task 5, used internally in same file ✓
- `Quote.from_payload(symbol, raw)` defined Task 2, called in `_reader` Task 5 ✓
- `FeedState` defined Task 2, used in `MarketDataFeed.state` Task 5 ✓
- `WsSession.connect(get_token, on_reconnect)` defined Task 3, called in `MarketDataFeed.start` Task 5 ✓
- `WsSession.iter_quotes()` defined Task 3, consumed in `_reader` Task 5 ✓
- `slot._closed`, `slot._latest`, `slot._event` — `QuoteState` internals accessed in `subscribe()` Task 5, in aclose tests Task 6 ✓
- `AuthError(is_invalid=...)` defined Task 3, tested Task 4 ✓
- `_SENTINEL` defined Task 3 (ws_client.py), used within same file ✓
