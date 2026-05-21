"""Sprint_01 tests for new WsHub functionality."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trader.api.ws_hub import WsHub, _TIMEFRAME_NAMES, _TF_HISTORY_DAYS
from trader.md.feed import MarketDataFeed


def make_mock_feed():
    feed = AsyncMock(spec=MarketDataFeed)
    feed.add_symbol = AsyncMock()

    async def fake_subscribe(symbol: str):
        await asyncio.sleep(9999)
        return
        yield

    feed.subscribe = fake_subscribe
    return feed


# --- _TIMEFRAME_NAMES and _TF_HISTORY_DAYS coverage ---

def test_timeframe_names_has_required_values():
    assert _TIMEFRAME_NAMES[1] == "TIME_FRAME_M1"
    assert _TIMEFRAME_NAMES[5] == "TIME_FRAME_M5"
    assert _TIMEFRAME_NAMES[9] == "TIME_FRAME_M15"
    assert _TIMEFRAME_NAMES[11] == "TIME_FRAME_M30"
    assert _TIMEFRAME_NAMES[12] == "TIME_FRAME_H1"
    assert _TIMEFRAME_NAMES[13] == "TIME_FRAME_H2"
    assert _TIMEFRAME_NAMES[15] == "TIME_FRAME_H4"
    assert _TIMEFRAME_NAMES[19] == "TIME_FRAME_D"


def test_tf_history_days_increases_with_timeframe():
    assert _TF_HISTORY_DAYS[1] < _TF_HISTORY_DAYS[5]
    assert _TF_HISTORY_DAYS[5] < _TF_HISTORY_DAYS[9]
    assert _TF_HISTORY_DAYS[9] < _TF_HISTORY_DAYS[19]


# --- WsHub constructor defaults ---

def test_hub_default_timeframe():
    feed = make_mock_feed()
    hub = WsHub(feed)
    assert hub._timeframe == 5


def test_hub_custom_timeframe():
    feed = make_mock_feed()
    hub = WsHub(feed, timeframe=12)
    assert hub._timeframe == 12


def test_hub_account_id_stored():
    feed = make_mock_feed()
    hub = WsHub(feed, account_id="2035452")
    assert hub._account_id == "2035452"


# --- _handle_subscribe ---

async def test_handle_subscribe_noop_same_symbol_and_tf():
    feed = make_mock_feed()
    hub = WsHub(feed, mvp_symbol="GZM6@RTSX", timeframe=5)
    hub._bars_history = [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    hub._broadcast = AsyncMock()

    await hub._handle_subscribe({"type": "subscribe", "symbol": "GZM6@RTSX", "timeframe": 5})

    hub._broadcast.assert_awaited_once()
    msg = hub._broadcast.call_args[0][0]
    assert msg["type"] == "ohlc_history"


async def test_handle_subscribe_changes_timeframe():
    feed = make_mock_feed()
    hub = WsHub(feed, mvp_symbol="GZM6@RTSX", timeframe=5)
    hub._broadcast = AsyncMock()

    async def fake_fetch(symbol, timeframe=None):
        return [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    hub._fetch_history = fake_fetch

    await hub._handle_subscribe({"type": "subscribe", "symbol": "GZM6@RTSX", "timeframe": 12})

    assert hub._timeframe == 12
    hub._broadcast.assert_awaited()
    msg = hub._broadcast.call_args[0][0]
    assert msg["type"] == "ohlc_history"


async def test_handle_subscribe_changes_symbol():
    feed = make_mock_feed()
    hub = WsHub(feed, mvp_symbol="GZM6@RTSX", timeframe=5)
    hub._broadcast = AsyncMock()

    async def fake_fetch(symbol, timeframe=None):
        return []

    hub._fetch_history = fake_fetch

    await hub._handle_subscribe({"type": "subscribe", "symbol": "RIM6@RTSX", "timeframe": 5})

    assert hub._mvp_symbol == "RIM6@RTSX"
    feed.add_symbol.assert_awaited_with("RIM6@RTSX")


async def test_handle_subscribe_cancels_existing_bars_task():
    feed = make_mock_feed()
    hub = WsHub(feed, mvp_symbol="GZM6@RTSX", timeframe=5)
    hub._broadcast = AsyncMock()

    cancelled = False

    async def long_task():
        nonlocal cancelled
        try:
            await asyncio.sleep(9999)
        except asyncio.CancelledError:
            cancelled = True
            raise

    hub._bars_task = asyncio.create_task(long_task())
    await asyncio.sleep(0)

    async def fake_fetch(symbol, timeframe=None):
        return []

    hub._fetch_history = fake_fetch

    await hub._handle_subscribe({"type": "subscribe", "symbol": "GZM6@RTSX", "timeframe": 12})
    await asyncio.sleep(0.01)

    assert cancelled


# --- _fetch_history uses correct timeframe name ---

async def test_fetch_history_uses_tf_name_in_params():
    feed = make_mock_feed()
    hub = WsHub(
        feed,
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="tok"),
        timeframe=5,
    )

    captured_params = {}

    async def mock_get(*args, **kwargs):
        captured_params.update(kwargs.get("params", {}))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"bars": []}
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)
        mock_client_cls.return_value = mock_http

        await hub._fetch_history("GZM6@RTSX", timeframe=9)

    assert captured_params.get("timeframe") == "TIME_FRAME_M15"


async def test_fetch_history_default_tf_from_hub():
    feed = make_mock_feed()
    hub = WsHub(
        feed,
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="tok"),
        timeframe=12,
    )

    captured_params = {}

    async def mock_get(*args, **kwargs):
        captured_params.update(kwargs.get("params", {}))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"bars": []}
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)
        mock_client_cls.return_value = mock_http

        await hub._fetch_history("GZM6@RTSX")

    assert captured_params.get("timeframe") == "TIME_FRAME_H1"


# --- _fetch_orders ---

async def test_fetch_orders_parses_active_orders():
    feed = make_mock_feed()
    hub = WsHub(
        feed,
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="tok"),
        account_id="2035452",
    )

    finam_orders = {
        "orders": [
            {
                "order_id": "ord-1",
                "symbol": "GZM6@RTSX",
                "side": "SIDE_BUY",
                "status": "ORDER_STATUS_ACTIVE",
                "quantity": {"value": "2"},
                "limit_price": {"value": "12000.0"},
            },
            {
                "order_id": "ord-2",
                "symbol": "GZM6@RTSX",
                "side": "SIDE_SELL",
                "status": "ORDER_STATUS_FILLED",  # should be filtered out
                "quantity": {"value": "1"},
                "limit_price": {"value": "12500.0"},
            },
        ]
    }

    async def mock_get(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = finam_orders
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)
        mock_client_cls.return_value = mock_http

        result = await hub._fetch_orders()

    assert len(result) == 1
    o = result[0]
    assert o["order_id"] == "ord-1"
    assert o["side"] == "buy"
    assert o["price"] == 12000.0
    assert o["qty"] == 2


async def test_fetch_orders_returns_empty_on_error():
    feed = make_mock_feed()
    hub = WsHub(
        feed,
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="tok"),
        account_id="2035452",
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=Exception("network error"))
        mock_client_cls.return_value = mock_http

        result = await hub._fetch_orders()

    assert result == []


# --- _fetch_recent_trades ---

async def test_fetch_recent_trades_parses_trades():
    feed = make_mock_feed()
    hub = WsHub(
        feed,
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="tok"),
        account_id="2035452",
    )

    finam_trades = {
        "trades": [
            {
                "trade_id": "t-1",
                "symbol": "GZM6@RTSX",
                "side": "SIDE_BUY",
                "price": {"value": "12100.0"},
                "timestamp": "2026-05-21T10:30:00Z",
            }
        ]
    }

    async def mock_get(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = finam_trades
        return resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)
        mock_client_cls.return_value = mock_http

        result = await hub._fetch_recent_trades()

    assert len(result) == 1
    t = result[0]
    assert t["trade_id"] == "t-1"
    assert t["side"] == "buy"
    assert t["price"] == 12100.0
    assert t["time"] > 0


async def test_fetch_recent_trades_returns_empty_on_error():
    feed = make_mock_feed()
    hub = WsHub(
        feed,
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="tok"),
        account_id="2035452",
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=Exception("timeout"))
        mock_client_cls.return_value = mock_http

        result = await hub._fetch_recent_trades()

    assert result == []


# --- _bars_broadcast_loop symbol filter ---

async def test_bars_broadcast_loop_skips_wrong_symbol():
    feed = make_mock_feed()
    hub = WsHub(feed)
    hub._bars_history = []

    broadcasts: list[dict] = []
    hub._broadcast = AsyncMock(side_effect=broadcasts.append)

    bars_stream = MagicMock()

    async def fake_iter():
        yield {"symbol": "RIM6@RTSX", "time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        yield {"symbol": "GZM6@RTSX", "time": 2, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2}
        await asyncio.sleep(9999)

    bars_stream.iter_bars = fake_iter
    hub._bars_stream = bars_stream

    task = asyncio.create_task(hub._bars_broadcast_loop("GZM6@RTSX"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await task

    ohlc_msgs = [m for m in broadcasts if m.get("type") == "ohlc_update"]
    assert len(ohlc_msgs) == 1
    assert ohlc_msgs[0]["symbol"] == "GZM6@RTSX"
    assert ohlc_msgs[0]["time"] == 2
