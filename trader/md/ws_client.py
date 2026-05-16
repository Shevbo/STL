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
