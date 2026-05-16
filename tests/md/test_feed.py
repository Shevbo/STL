import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

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
    await feed.add_symbol("GZM6@RTSX")  # slot must exist before reader processes frame
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
    await feed.add_symbol("GZM6@RTSX")  # slot must exist before reader processes frame
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
    await feed.add_symbol("GZM6@RTSX")  # slot must exist before reader processes frames

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
