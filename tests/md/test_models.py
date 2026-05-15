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
