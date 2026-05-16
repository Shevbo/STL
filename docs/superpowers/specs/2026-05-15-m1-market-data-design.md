# M1 Market Data — Design Spec

**Date:** 2026-05-15
**Module:** `trader/md/`
**Status:** Approved (Opus cross-inspection × 5 sections)

---

## Goal

Provide a reliable, reconnecting WebSocket feed of real-time QUOTES from Finam Trade API, with a conflated-slot consumer interface suited for tick-level trading strategies.

## Architecture

Three layers with a clear separation of concerns:

1. **WsSession** (`ws_client.py`) — raw WebSocket transport: connect, auth, send/recv dispatch, ping/pong, reconnect with backoff
2. **MarketDataFeed** (`feed.py`) — lifecycle management: symbol subscriptions, conflated slot per symbol, watchdog, FeedState, on_raw hook
3. **Models** (`models.py`) — `Quote` frozen dataclass + `Quote.from_payload()` + `FeedState` enum

Consumers interact only with `MarketDataFeed`. `WsSession` is an internal dependency.

## Tech Stack

- Python 3.12, asyncio
- `websockets >= 13.1`
- `orjson >= 3.10` (fast JSON, used in WsSession reader)
- `structlog` (existing)
- `pytest`, `pytest-asyncio` (asyncio_mode=auto), `pytest-mock`

---

## File Structure

```
trader/md/
├── __init__.py        # re-export MarketDataFeed, Quote, FeedState
├── models.py          # Quote (frozen dataclass) + FeedState enum
├── ws_client.py       # WsSession
└── feed.py            # QuoteState + MarketDataFeed

tests/md/
├── __init__.py
├── test_models.py     # Quote.from_payload — Decimal envelope, edge cases, hypothesis fuzz
├── test_ws_client.py  # happy-path connect/subscribe/iter/close
├── test_reconnect.py  # network drop, auth failure, watchdog STALE, backoff cap
├── test_feed.py       # conflated semantics, aclose, cleanup
└── test_integration.py  # @pytest.mark.integration — real Finam WS
```

---

## Section 1: Data Models (`models.py`)

### FeedState

```python
from enum import Enum

class FeedState(Enum):
    CONNECTING = "connecting"
    LIVE = "live"
    STALE = "stale"
    CLOSED = "closed"
```

State transitions:
- `CONNECTING → LIVE` — first Quote received after connect
- `LIVE → STALE` — watchdog fires (5s without any Quote)
- `STALE → LIVE` — reconnect succeeded + first Quote received
- `any → CLOSED` — explicit `aclose()` or unrecoverable error (auth 401 invalid token)

**Trading layer policy:** must refuse new position entry when `state != LIVE`. After `STALE → LIVE` recovery, first N ticks should be treated as unreliable (hysteresis — policy of the trading layer, not enforced by feed).

### Quote

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    bid_size: int
    ask: Decimal
    ask_size: int
    last: Decimal
    last_size: int
    timestamp: datetime  # UTC-aware

    @classmethod
    def from_payload(cls, symbol: str, data: dict) -> "Quote":
        def dec(obj) -> Decimal:
            # Finam envelope: {"value": "123.45"} or plain string
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

`Quote` is frozen — safe to store and pass between coroutines without copying. All price fields are `Decimal` (no float rounding). `timestamp` is always UTC-aware.

`from_payload` handles both `{"value": "N"}` Finam Decimal envelope and plain strings.

---

## Section 2: WsSession (`ws_client.py`)

### Constants

```python
WS_URL = "wss://api.finam.ru:443/ws"
PING_INTERVAL = 3.0     # seconds — must be < watchdog_secs
PING_TIMEOUT = 2.0      # seconds — PING_INTERVAL + PING_TIMEOUT ≤ watchdog_secs
RECONNECT_BASE = 0.1    # seconds — aggressive first attempts
RECONNECT_MAX = 60.0    # seconds — backoff ceiling
RECONNECT_JITTER = 1.0  # full jitter multiplier (AWS style)
```

PING_INTERVAL + PING_TIMEOUT = 5s — network drop detected within watchdog window.

### Interface

```python
class WsSession:
    async def connect(self, get_token: Callable[[], Awaitable[str]]) -> None: ...
    async def subscribe(self, symbol: str) -> None: ...   # idempotent, waits for ack
    async def unsubscribe(self, symbol: str) -> None: ...
    async def iter_quotes(self) -> AsyncIterator[dict]: ... # data-plane raw dicts
    async def close(self, code: int = 1000) -> None: ...
    @property
    def connected(self) -> bool: ...

    # metrics (read-only)
    messages_received: int
    reconnect_count: int
    last_message_age_ms: float
    ping_rtt_ms: float
```

### Internal architecture

Single reader task dispatches incoming frames by message type:
- **control-plane:** auth ack, subscribe ack → `asyncio.Queue(maxsize=20)`
- **data-plane:** quotes → `asyncio.Queue(maxsize=100)`

**Queue overflow policy:** drop oldest entry + log warning `ws.queue_overflow` + increment metric. Never block the reader.

**orjson** used for all JSON parsing in the reader task.

### Reconnect strategy

Full jitter backoff (AWS style):
```python
delay = random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))
```

On reconnect:
1. Get fresh token (may have expired during outage)
2. Reconnect WebSocket
3. Verify subscribe ack for all active symbols before marking LIVE
4. Feed calls `_resubscribe_all()` — see Feed contract below

**Auth 401 handling:**
- Expired token → refresh once + retry
- Invalid token → do not retry, raise → Feed transitions to CLOSED

**Subscribe ack timeout:** retry 3 times, then raise.

**Maintenance window (05:00–06:15 МСК):** standard backoff applies — after 75 minutes of unavailability, backoff reaches ceiling (60s) and waits quietly without request storms.

### Make-before-break (future, not MVP)

Proactive 24h reconnect deferred — YAGNI until a confirmed memory leak or token-in-socket expiry issue is observed in production. Add as TODO comment.

---

## Section 3: MarketDataFeed (`feed.py`)

### QuoteState (internal)

```python
class QuoteState:
    _latest: Quote | None = None
    _event: asyncio.Event          # set() on each new Quote
    _closed: bool = False          # set on aclose()

    def update(self, quote: Quote) -> None:
        self._latest = quote
        self._event.set()          # wake all waiters

    def next_event(self) -> asyncio.Event:
        self._event.clear()
        return self._event
```

Classic conflated pattern — no race conditions, no Future list.

### MarketDataFeed

```python
class MarketDataFeed:
    def __init__(
        self,
        ws: WsSession,
        watchdog_secs: float = 5.0,
        on_raw: Callable[[dict], None] | None = None,
    ): ...

    async def start(self) -> None:
        """Connect ws, start reader task + watchdog task."""

    async def add_symbol(self, symbol: str) -> None:
        """Add symbol subscription. Idempotent. Sends subscribe to ws."""

    def latest(self, symbol: str) -> Quote | None:
        """Last known quote or None. Synchronous, lock-free.
        NOTE: only call from within the same event loop."""

    async def subscribe(self, symbol: str) -> AsyncIterator[Quote]:
        """Async iterator yielding each new Quote as it arrives.
        Conflated: if 10 ticks arrive while consumer sleeps, only
        the latest is yielded on wake-up."""

    @property
    def state(self) -> FeedState: ...

    async def aclose(self) -> None: ...
```

### Reader task (internal)

```
iter_quotes() from WsSession
  → try: raw_dict = orjson.loads(frame)
    except orjson.JSONDecodeError: log md.decode_error, skip
  → if on_raw:
        try: on_raw(raw_dict)
        except: log md.on_raw_error, continue  # hook must not kill reader
  → try: quote = Quote.from_payload(symbol, raw_dict)
    except: log md.parse_error + metric, skip
  → if quote.timestamp < slot._latest.timestamp: log md.out_of_order, metric, skip
  → slot.update(quote)
  → reset watchdog event
```

### subscribe() implementation

```python
async def subscribe(self, symbol: str) -> AsyncIterator[Quote]:
    if symbol not in self._slots:
        await self.add_symbol(symbol)
    slot = self._slots[symbol]
    try:
        while self._running and not slot._closed:
            await slot._event.wait()
            slot._event.clear()
            if slot._latest is not None:
                yield slot._latest
    finally:
        # cleanup on break / task cancellation
        pass  # Event-based: no dangling Futures to cancel
```

### Watchdog task (internal)

```python
async def _watchdog(self) -> None:
    while self._running:
        try:
            await asyncio.wait_for(self._heartbeat.wait(), self._watchdog_secs)
            self._heartbeat.clear()
            if self._state == FeedState.STALE:
                self._state = FeedState.LIVE
        except asyncio.TimeoutError:
            self._state = FeedState.STALE
            log.warning("md.watchdog.stale")
            # metric: stale_transitions += 1
```

**TODO:** per-symbol watchdog — single global watchdog hides scenario where one symbol stops ticking while others remain live. Acceptable for single-symbol MVP.

### Feed ↔ WsSession reconnect contract

`WsSession` calls `feed._resubscribe_all()` after each successful reconnect. `MarketDataFeed` stores `_active_symbols: set[str]` and resends all subscriptions. This is the explicit contract — not delegated to external code.

### Shutdown sequence

```python
async def aclose(self) -> None:
    self._running = False
    self._watchdog_task.cancel()
    await self._ws.close(code=1000)   # graceful: reader exits naturally via ConnectionClosed
    try:
        await asyncio.wait_for(self._reader_task, timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        self._reader_task.cancel()
    # drain
    self._state = FeedState.CLOSED
    for slot in self._slots.values():
        slot._closed = True
        slot._event.set()             # wake subscribers → StopAsyncIteration
```

Order matters: close WS before cancelling reader — reader exits naturally through `ConnectionClosed`, preserving last ticks in buffer. `_closed = True` guard prevents `anext()` calls after `aclose()` from hanging.

---

## Section 4: Error Handling Matrix

| Layer | Event | Action |
|---|---|---|
| WsSession reader | orjson decode error | log `ws.decode_error`, skip frame |
| WsSession | network drop / ping timeout | backoff reconnect, log `ws.reconnect` |
| WsSession | auth 401 expired token | refresh token + retry once |
| WsSession | auth 401 invalid token | raise, feed → CLOSED |
| WsSession | subscribe ack timeout | retry 3×, then raise |
| WsSession | data queue full | drop oldest, log `ws.queue_overflow` + metric |
| Feed reader | `Quote.from_payload` exception | log `md.parse_error` + metric, skip tick |
| Feed reader | `on_raw` hook exception | log `md.on_raw_error`, continue |
| Feed reader | out-of-order timestamp | log `md.out_of_order` + metric, skip tick |
| Feed watchdog | 5s without Quote | `state = STALE`, log + metric `stale_transitions` |
| Feed | reader task crashed | `state = CLOSED`, cancel watchdog, wake all slots |

---

## Section 5: Testing Strategy

### test_models.py

- `test_quote_from_payload_decimal_envelope` — `{"value": "N"}` format
- `test_quote_from_payload_plain_string` — plain decimal string fallback
- `test_quote_timestamp_is_utc_aware` — `timestamp.tzinfo is not None`
- `test_quote_is_frozen` — `dataclasses.FrozenInstanceError` on mutation
- Hypothesis fuzz: random dicts with null fields, negative sizes, malformed Decimal strings → must raise predictably, not crash unexpectedly

### test_ws_client.py

- `test_happy_path_connect_subscribe_iter_close`
- `test_ping_timeout_triggers_reconnect`
- `test_subscribe_ack_missing_retries_3_times_then_raises`
- `test_queue_overflow_drops_oldest_not_blocks`

### test_reconnect.py

- `test_network_drop_backoff_resubscribe_live`
- `test_auth_401_invalid_token_no_retry`
- `test_auth_401_expired_token_refresh_and_retry`
- `test_watchdog_stale_then_reconnect_live`
- `test_backoff_cap_100_failures_no_request_storm` — verify delay never exceeds RECONNECT_MAX

### test_feed.py

- `test_latest_returns_none_before_first_tick`
- `test_subscribe_conflation_slow_consumer` — 10 ticks arrive, consumer gets only latest
- `test_subscribe_break_no_event_leak` — `break` → no dangling Events
- `test_aclose_during_iteration` — parallel `aclose()` while consumer in `anext()` → StopAsyncIteration
- `test_aclose_then_anext_does_not_hang` — `_closed` flag guard
- `test_conflation_under_load` — 10k ticks/sec simulated, slow consumer (sleep 100ms), verify no OOM and reader not delayed

### test_integration.py (`@pytest.mark.integration`)

```python
@pytest.mark.timeout(60)
async def test_live_quote_received(feed):
    await feed.add_symbol("GZM6@RTSX")
    async for quote in feed.subscribe("GZM6@RTSX"):
        assert quote.bid > 0
        assert feed.state == FeedState.LIVE
        break

@pytest.mark.timeout(60)
async def test_feed_state_is_live_after_start(feed):
    await feed.add_symbol("GZM6@RTSX")
    await asyncio.wait_for(
        _wait_for_state(feed, FeedState.LIVE), timeout=30.0
    )
    assert feed.state == FeedState.LIVE
```

---

## Dependencies to add to pyproject.toml

```toml
websockets = "^13.1"
orjson = "^3.10"
```

Dev deps (if not already present):
```toml
hypothesis = "^6"
pytest-timeout = "^2"
```

---

## Out of Scope (M1)

- ORDER_BOOK subscription (QUOTES sufficient for positional trading)
- Per-symbol watchdog timeout (single symbol MVP, TODO comment)
- Proactive 24h reconnect (YAGNI, TODO comment)
- Sequence gap detection (unknown if Finam QUOTES include seq)
- Clock skew detection beyond out-of-order timestamp check
