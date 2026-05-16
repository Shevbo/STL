"""
Integration tests — require real Finam credentials + live market hours.

Setup:
  set -a && source ~/.shectory_trade.env && set +a

Run:
  poetry run pytest tests/md/test_integration.py -v -m integration
"""
import asyncio

import pytest

from trader.auth.client import AsyncAuthClient
from trader.config import Settings
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
    async for quote in feed.subscribe("GZM6@RTSX"):
        assert quote is not None
        break
