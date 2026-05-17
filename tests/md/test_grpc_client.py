# tests/md/test_grpc_client.py
"""Unit tests for grpc_client — quote_from_proto and QuoteStream."""

import sys

sys.path.insert(0, "trader/proto/gen")

from datetime import datetime, timezone
from decimal import Decimal


from trader.md.grpc_client import quote_from_proto
from trader.md.models import Quote


def _make_pb_quote(
    symbol="GZM6@RTSX",
    bid="100.5",
    ask="100.6",
    last="100.55",
    bid_size="10",
    ask_size="5",
    last_size="3",
    ts_sec=1_716_000_000,
):
    """Build a real proto Quote object."""
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    from google.protobuf import timestamp_pb2

    ts = timestamp_pb2.Timestamp()
    ts.seconds = ts_sec
    ts.nanos = 0

    def _dec(v):
        from google.type import decimal_pb2

        d = decimal_pb2.Decimal()
        d.value = v
        return d

    q = md_pb2.Quote()
    q.symbol = symbol
    q.timestamp.CopyFrom(ts)
    q.bid.CopyFrom(_dec(bid))
    q.ask.CopyFrom(_dec(ask))
    q.last.CopyFrom(_dec(last))
    q.bid_size.CopyFrom(_dec(bid_size))
    q.ask_size.CopyFrom(_dec(ask_size))
    q.last_size.CopyFrom(_dec(last_size))
    return q


def test_quote_from_proto_maps_symbol():
    pb = _make_pb_quote(symbol="SBER@MISX")
    raw = quote_from_proto(pb)
    assert raw["symbol"] == "SBER@MISX"


def test_quote_from_proto_maps_decimal_fields():
    pb = _make_pb_quote(bid="123.45", ask="123.50", last="123.47")
    raw = quote_from_proto(pb)
    assert raw["bid"] == "123.45"
    assert raw["ask"] == "123.50"
    assert raw["last"] == "123.47"


def test_quote_from_proto_maps_size_fields_to_int():
    pb = _make_pb_quote(bid_size="50", ask_size="20", last_size="7")
    raw = quote_from_proto(pb)
    assert raw["bid_size"] == 50
    assert raw["ask_size"] == 20
    assert raw["last_size"] == 7


def test_quote_from_proto_timestamp_is_utc_iso():
    pb = _make_pb_quote(ts_sec=1_716_000_000)
    raw = quote_from_proto(pb)
    # Must be parseable by Quote.from_payload
    dt = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
    assert dt.tzinfo is not None
    assert dt == datetime.fromtimestamp(1_716_000_000, tz=timezone.utc)


def test_quote_from_proto_empty_decimal_becomes_zero():
    """Empty decimal (no bid on market) maps to '0', not crash."""
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    from google.protobuf import timestamp_pb2

    pb = md_pb2.Quote()
    pb.symbol = "X"
    pb.timestamp.CopyFrom(timestamp_pb2.Timestamp(seconds=1_716_000_000))
    # bid/ask/last left unset → empty Decimal with value=""
    raw = quote_from_proto(pb)
    assert raw["bid"] == "0"
    assert raw["bid_size"] == 0


def test_quote_from_proto_is_parseable_by_from_payload():
    """End-to-end: proto → dict → Quote dataclass."""
    pb = _make_pb_quote()
    raw = quote_from_proto(pb)
    quote = Quote.from_payload(raw["symbol"], raw)
    assert quote.bid == Decimal("100.5")
    assert quote.ask == Decimal("100.6")
    assert quote.last == Decimal("100.55")
    assert quote.bid_size == 10
    assert quote.ask_size == 5
    assert quote.last_size == 3
    assert quote.timestamp.tzinfo is not None


# --- QuoteStream unit tests ---

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_subscribe_response(quotes_data: list[dict]):
    """Build a SubscribeQuoteResponse proto with Quote list."""
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    resp = md_pb2.SubscribeQuoteResponse()
    for d in quotes_data:
        pb_q = _make_pb_quote(**d)
        resp.quote.append(pb_q)
    return resp


def _make_error_response(code: int, description: str):
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    resp = md_pb2.SubscribeQuoteResponse()
    resp.error.code = code
    resp.error.description = description
    return resp


def _make_stub(responses: list, *, raise_after: Exception | None = None):
    """Return a fake stub whose SubscribeQuote is an async generator."""
    stub = MagicMock()

    async def _gen(*args, **kwargs):
        for r in responses:
            yield r
        if raise_after:
            raise raise_after
        # else: clean exit (simulates server closing stream)

    stub.SubscribeQuote = MagicMock(return_value=_gen())
    return stub


async def test_subscribe_starts_task_and_quotes_appear_in_iter_quotes():
    from trader.md.grpc_client import QuoteStream

    resp = _make_subscribe_response([{}])  # one quote with defaults

    qs = QuoteStream()
    await qs.start(get_token=AsyncMock(return_value="tok"))

    stub = _make_stub([resp])

    with (
        patch("trader.md.grpc_client.grpc.ssl_channel_credentials"),
        patch("trader.md.grpc_client.grpc.aio.secure_channel", return_value=AsyncMock()),
        patch("trader.md.grpc_client.MarketDataServiceStub", return_value=stub),
    ):
        await qs.subscribe("GZM6@RTSX")
        await asyncio.sleep(0.05)

        received = []
        async for raw in qs.iter_quotes():
            received.append(raw)
            break

    await qs.close()
    assert len(received) == 1
    assert received[0]["symbol"] == "GZM6@RTSX"


async def test_stream_error_in_payload_is_skipped():
    """StreamError in response body is logged and skipped — does not raise."""
    from trader.md.grpc_client import QuoteStream

    err_resp = _make_error_response(code=503, description="service unavailable")
    quote_resp = _make_subscribe_response([{}])

    qs = QuoteStream()
    await qs.start(get_token=AsyncMock(return_value="tok"))

    stub = _make_stub([err_resp, quote_resp])

    with (
        patch("trader.md.grpc_client.grpc.ssl_channel_credentials"),
        patch("trader.md.grpc_client.grpc.aio.secure_channel", return_value=AsyncMock()),
        patch("trader.md.grpc_client.MarketDataServiceStub", return_value=stub),
    ):
        await qs.subscribe("GZM6@RTSX")
        await asyncio.sleep(0.05)
        received = []
        async for raw in qs.iter_quotes():
            received.append(raw)
            break

    await qs.close()
    # Must have received the valid quote (error response was skipped)
    assert len(received) == 1


async def test_backoff_cap_never_exceeds_reconnect_max():
    from trader.md.grpc_client import RECONNECT_BASE, RECONNECT_MAX, _backoff

    for attempt in range(100):
        delay = _backoff(attempt)
        assert delay <= RECONNECT_MAX, f"attempt {attempt}: delay {delay} > {RECONNECT_MAX}"


async def test_close_drains_sentinel_so_iter_quotes_exits():
    """close() must unblock an in-progress iter_quotes() call."""
    from trader.md.grpc_client import QuoteStream

    qs = QuoteStream()
    await qs.start(get_token=AsyncMock(return_value="tok"))

    # No subscribe — just test that close() sends sentinel
    exited = asyncio.Event()

    async def _consume():
        async for _ in qs.iter_quotes():
            pass
        exited.set()

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.01)
    await qs.close()
    await asyncio.wait_for(exited.wait(), timeout=2.0)
    task.cancel()
