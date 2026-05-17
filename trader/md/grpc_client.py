import asyncio
import random
from collections.abc import AsyncIterator, Callable, Awaitable
from datetime import timezone

import grpc
import grpc.aio
import structlog

# Bootstrap grpc namespace so generated stubs are importable as grpc.tradeapi.*
_GEN_GRPC = str(
    __import__("pathlib").Path(__file__).parent.parent / "proto" / "gen" / "grpc"
)
if _GEN_GRPC not in grpc.__path__:
    grpc.__path__.append(_GEN_GRPC)

from grpc.tradeapi.v1.marketdata.marketdata_service_pb2_grpc import MarketDataServiceStub  # noqa: E402
from grpc.tradeapi.v1.marketdata.marketdata_service_pb2 import SubscribeQuoteRequest  # noqa: E402

log = structlog.get_logger()

GRPC_TARGET = "api.finam.ru:443"
RECONNECT_BASE = 0.1
RECONNECT_MAX = 60.0

CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]

_SENTINEL = object()


def _backoff(attempt: int) -> float:
    return random.uniform(0, min(RECONNECT_BASE * (2**attempt), RECONNECT_MAX))


def quote_from_proto(pb) -> dict:
    """Convert a proto Quote to the dict format expected by Quote.from_payload()."""
    ts = pb.timestamp.ToDatetime(tzinfo=timezone.utc)
    return {
        "symbol": pb.symbol,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "bid": pb.bid.value or "0",
        "bid_size": int(float(pb.bid_size.value or "0")),
        "ask": pb.ask.value or "0",
        "ask_size": int(float(pb.ask_size.value or "0")),
        "last": pb.last.value or "0",
        "last_size": int(float(pb.last_size.value or "0")),
    }


class QuoteStream:
    """gRPC streaming client for Finam market data. Same interface as WsSession."""

    def __init__(self) -> None:
        self._channel: grpc.aio.Channel | None = None
        self._get_token: Callable[[bool], Awaitable[str]] | None = None
        self._data_q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self.messages_received: int = 0

    async def start(self, get_token: Callable[[bool], Awaitable[str]]) -> None:
        self._get_token = get_token
        self._running = True

    def _ensure_channel(self) -> None:
        if self._channel is None:
            creds = grpc.ssl_channel_credentials()
            self._channel = grpc.aio.secure_channel(GRPC_TARGET, creds, options=CHANNEL_OPTIONS)

    async def subscribe(self, symbol: str) -> None:
        if symbol in self._stream_tasks and not self._stream_tasks[symbol].done():
            return
        task = asyncio.create_task(self._stream_symbol(symbol))
        self._stream_tasks[symbol] = task

    async def _stream_symbol(self, symbol: str) -> None:
        self._ensure_channel()
        attempt = 0
        while self._running:
            try:
                force = attempt > 0  # force-refresh token after first failure
                token = await self._get_token(force)
                metadata = [("authorization", token)]
                stub = MarketDataServiceStub(self._channel)
                req = SubscribeQuoteRequest(symbols=[symbol])
                async for resp in stub.SubscribeQuote(req, metadata=metadata):
                    if resp.error.code:
                        log.warning(
                            "md.stream_error",
                            symbol=symbol,
                            code=resp.error.code,
                            description=resp.error.description,
                        )
                        continue
                    for pb_quote in resp.quote:
                        self._put_data(quote_from_proto(pb_quote))
                        self.messages_received += 1
                attempt = 0  # clean stream exit — reconnect immediately, no delay
            except grpc.aio.AioRpcError as exc:
                if not self._running:
                    return
                log.warning(
                    "md.rpc_error",
                    symbol=symbol,
                    code=str(exc.code()),
                    attempt=attempt,
                )
                delay = _backoff(attempt)
                await asyncio.sleep(delay)
                attempt += 1
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if not self._running:
                    return
                log.error("md.stream_crashed", symbol=symbol, exc=str(exc))
                delay = _backoff(attempt)
                await asyncio.sleep(delay)
                attempt += 1

    def _put_data(self, msg: dict) -> None:
        if self._data_q.full():
            try:
                self._data_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            log.warning("md.queue_overflow")
        self._data_q.put_nowait(msg)

    async def iter_quotes(self) -> AsyncIterator[dict]:
        while True:
            item = await self._data_q.get()
            if item is _SENTINEL:
                self._data_q.put_nowait(_SENTINEL)  # re-enqueue for any other consumers
                return
            yield item

    async def close(self, code: int = 1000) -> None:
        self._running = False
        for task in self._stream_tasks.values():
            task.cancel()
        if self._stream_tasks:
            await asyncio.gather(*self._stream_tasks.values(), return_exceptions=True)
        self._stream_tasks.clear()
        if self._channel:
            await self._channel.close()
        self._data_q.put_nowait(_SENTINEL)
