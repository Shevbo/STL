import asyncio
import random
import sys
from collections.abc import AsyncIterator, Callable, Awaitable
from datetime import timezone

import grpc
import grpc.aio
import structlog

# Bootstrap grpc namespace so generated stubs are importable as grpc.tradeapi.*
_GEN_GRPC = str(__import__('pathlib').Path(__file__).parent.parent / "proto" / "gen" / "grpc")
if _GEN_GRPC not in grpc.__path__:
    grpc.__path__.append(_GEN_GRPC)

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
    return random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))


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
