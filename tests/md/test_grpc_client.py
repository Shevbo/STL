# tests/md/test_grpc_client.py
"""Unit tests for grpc_client — quote_from_proto and QuoteStream."""
import sys
sys.path.insert(0, "trader/proto/gen")

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.md.grpc_client import quote_from_proto
from trader.md.models import Quote


def _make_pb_quote(symbol="GZM6@RTSX", bid="100.5", ask="100.6",
                   last="100.55", bid_size="10", ask_size="5", last_size="3",
                   ts_sec=1_716_000_000):
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
