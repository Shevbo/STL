import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocketDisconnect

from trader.api.ws_hub import WsHub, _TF_HISTORY_DAYS, _TIMEFRAME_NAMES
from trader.md.feed import MarketDataFeed
from trader.md.models import Quote
from trader.pos.models import AccountSummary, Position


def make_quote(bid: str = "100.0") -> Quote:
    return Quote(
        symbol="GZM6@RTSX",
        bid=Decimal(bid),
        bid_size=10,
        ask=Decimal("100.1"),
        ask_size=5,
        last=Decimal("100.05"),
        last_size=3,
        timestamp=datetime.now(timezone.utc),
    )


def make_mock_feed(quotes: list[Quote] | None = None):
    feed = AsyncMock(spec=MarketDataFeed)
    _quotes = quotes or []

    async def fake_subscribe(symbol: str):
        for q in _quotes:
            yield q
        await asyncio.sleep(9999)

    feed.subscribe = fake_subscribe
    feed.add_symbol = AsyncMock()
    return feed


def make_mock_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()

    async def _iter_disconnects():
        raise WebSocketDisconnect()
        yield  # make generator

    ws.iter_text = _iter_disconnects
    return ws


# --- _put conflation ---

async def test_put_adds_item_when_queue_not_full():
    feed = make_mock_feed()
    hub = WsHub(feed)
    q = asyncio.Queue(maxsize=2)
    hub._put(q, {"msg": "a"})
    assert q.qsize() == 1


async def test_put_drops_oldest_when_full():
    feed = make_mock_feed()
    hub = WsHub(feed)
    q = asyncio.Queue(maxsize=2)
    hub._put(q, {"msg": "a"})
    hub._put(q, {"msg": "b"})
    hub._put(q, {"msg": "c"})  # should drop "a"
    assert q.qsize() == 2
    first = q.get_nowait()
    assert first["msg"] == "b"


# --- _broadcast ---

async def test_broadcast_sends_to_all_clients():
    feed = make_mock_feed()
    hub = WsHub(feed)
    q1: asyncio.Queue = asyncio.Queue(maxsize=50)
    q2: asyncio.Queue = asyncio.Queue(maxsize=50)
    ws1, ws2 = MagicMock(), MagicMock()
    hub._clients[ws1] = q1
    hub._clients[ws2] = q2

    await hub._broadcast({"type": "quote"})

    assert q1.qsize() == 1
    assert q2.qsize() == 1


async def test_broadcast_no_clients_does_not_raise():
    feed = make_mock_feed()
    hub = WsHub(feed)
    await hub._broadcast({"type": "quote"})  # no exception


# --- start / stop ---

async def test_start_calls_add_symbol_for_each():
    feed = make_mock_feed()
    hub = WsHub(feed)
    await hub.start(["GZM6@RTSX", "SRM6@RTSX"])
    assert feed.add_symbol.call_count == 2
    feed.add_symbol.assert_any_call("GZM6@RTSX")
    feed.add_symbol.assert_any_call("SRM6@RTSX")
    await hub.stop()


async def test_stop_cancels_broadcast_tasks():
    feed = make_mock_feed()
    hub = WsHub(feed)
    await hub.start(["GZM6@RTSX"])
    assert len(hub._broadcast_tasks) == 1
    await hub.stop()
    await asyncio.sleep(0.01)
    assert hub._broadcast_tasks[0].cancelled()


async def test_stop_without_start_does_not_raise():
    feed = make_mock_feed()
    hub = WsHub(feed)
    await hub.stop()  # no exception


# --- quote message format ---

async def test_broadcast_loop_puts_quote_message_in_client_queue():
    q = make_quote(bid="101.5")
    feed = make_mock_feed(quotes=[q])
    hub = WsHub(feed)

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    ws = MagicMock()
    hub._clients[ws] = client_queue

    task = asyncio.create_task(hub._broadcast_loop("GZM6@RTSX"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await task

    assert client_queue.qsize() >= 1
    msg = client_queue.get_nowait()
    assert msg["type"] == "quote"
    assert msg["symbol"] == "GZM6@RTSX"
    assert msg["bid"] == 101.5
    assert "ask" in msg
    assert "timestamp" in msg


# --- position poll ---

async def test_pos_poll_broadcasts_position_update():
    feed = make_mock_feed()
    pos_client = AsyncMock()
    pos_client.get_portfolio.return_value = [
        Position(
            symbol="GZM6@RTSX",
            account_id="2035452",
            side="long",
            quantity=1,
            avg_price=Decimal("100"),
            current_price=Decimal("101"),
            var_margin=Decimal("1"),
        )
    ]
    hub = WsHub(feed, pos_client=pos_client)

    client_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    ws = MagicMock()
    hub._clients[ws] = client_queue

    task = asyncio.create_task(hub._pos_poll_loop(poll_interval=0.05))
    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await task

    assert client_queue.qsize() >= 1
    msg = client_queue.get_nowait()
    assert msg["type"] == "position_update"
    assert isinstance(msg["positions"], list)
    assert msg["positions"][0]["symbol"] == "GZM6@RTSX"


# --- connect / disconnect ---

async def test_connect_accepts_websocket():
    feed = make_mock_feed()
    hub = WsHub(feed)
    ws = make_mock_ws()

    await asyncio.wait_for(hub.connect(ws), timeout=2.0)
    ws.accept.assert_called_once()


async def test_connect_removes_client_on_disconnect():
    feed = make_mock_feed()
    hub = WsHub(feed)
    ws = make_mock_ws()

    await asyncio.wait_for(hub.connect(ws), timeout=2.0)
    assert ws not in hub._clients


async def test_connect_sends_queued_message():
    feed = make_mock_feed()
    hub = WsHub(feed)

    async def _iter_slow():
        await asyncio.sleep(0.05)
        raise WebSocketDisconnect()
        yield

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.iter_text = _iter_slow

    async def _enqueue():
        await asyncio.sleep(0.01)
        if ws in hub._clients:
            hub._put(hub._clients[ws], {"type": "quote"})

    await asyncio.gather(
        asyncio.wait_for(hub.connect(ws), timeout=2.0),
        _enqueue(),
    )
    all_calls = [c.args[0] for c in ws.send_json.call_args_list]
    assert {"type": "quote"} in all_calls


# --- Bug 3: initial service statuses sent on connect ---

async def test_connect_sends_initial_ok_for_all_services():
    feed = make_mock_feed()
    hub = WsHub(feed)

    async def _iter_slow():
        for _ in range(20):
            await asyncio.sleep(0)
        raise WebSocketDisconnect()
        yield

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.iter_text = _iter_slow

    await asyncio.wait_for(hub.connect(ws), timeout=2.0)

    sent = [c.args[0] for c in ws.send_json.call_args_list]
    service_msgs = [m for m in sent if m.get("type") == "service_status"]
    services = {m["service"] for m in service_msgs}
    assert services == {"auth", "tx", "oms", "pos", "audit"}
    assert all(m["status"] == "ok" for m in service_msgs)


# --- Bug 2: account broadcast in _pos_poll_loop ---

async def test_pos_poll_loop_broadcasts_account_message():
    feed = make_mock_feed()
    pos_client = AsyncMock()
    pos_client.get_portfolio = AsyncMock(return_value=[])
    pos_client.get_account_summary = AsyncMock(return_value=AccountSummary(
        deposit=Decimal("1793087.28"),
        free=Decimal("169281.99"),
        in_position=Decimal("1636734.23"),
        variation_margin=Decimal("-11344.44"),
    ))
    hub = WsHub(feed, pos_client=pos_client)
    hub._clients[object()] = asyncio.Queue()  # poll loop is a no-op with no clients

    broadcasts: list[dict] = []
    hub._broadcast = AsyncMock(side_effect=broadcasts.append)

    task = asyncio.create_task(hub._pos_poll_loop(poll_interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    account_msgs = [m for m in broadcasts if m.get("type") == "account"]
    assert len(account_msgs) >= 1
    msg = account_msgs[0]
    assert msg["deposit"] == pytest.approx(1793087.28)
    assert msg["free"] == pytest.approx(169281.99)
    assert msg["in_position"] == pytest.approx(1636734.23)
    assert msg["variation_margin"] == pytest.approx(-11344.44)


async def test_pos_poll_loop_continues_after_error():
    feed = make_mock_feed()
    pos_client = AsyncMock()
    pos_client.get_portfolio = AsyncMock(
        side_effect=[Exception("network error"), []]
    )
    pos_client.get_account_summary = AsyncMock(return_value=AccountSummary(
        deposit=Decimal("0"),
        free=Decimal("0"),
        in_position=Decimal("0"),
        variation_margin=Decimal("0"),
    ))
    hub = WsHub(feed, pos_client=pos_client)
    hub._clients[object()] = asyncio.Queue()  # poll loop is a no-op with no clients

    broadcasts: list[dict] = []
    hub._broadcast = AsyncMock(side_effect=broadcasts.append)

    task = asyncio.create_task(hub._pos_poll_loop(poll_interval=0.01))
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    pos_msgs = [m for m in broadcasts if m.get("type") == "position_update"]
    assert len(pos_msgs) >= 1


# --- _TIMEFRAME_NAMES and _TF_HISTORY_DAYS coverage ---

def test_timeframe_names_has_required_values():
    # Finam REST /bars supports only M1, M5, M15, D, W, MN — coarser intraday
    # frames (M30/H1/H2/H4) intentionally fall back to M15, H8 to D.
    assert _TIMEFRAME_NAMES[1] == "TIME_FRAME_M1"
    assert _TIMEFRAME_NAMES[5] == "TIME_FRAME_M5"
    assert _TIMEFRAME_NAMES[9] == "TIME_FRAME_M15"
    assert _TIMEFRAME_NAMES[11] == "TIME_FRAME_M15"   # M30 → M15 (unsupported by REST)
    assert _TIMEFRAME_NAMES[12] == "TIME_FRAME_M15"   # H1  → M15
    assert _TIMEFRAME_NAMES[15] == "TIME_FRAME_M15"   # H4  → M15
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

    # hub timeframe=12 (H1) maps to M15 — Finam REST /bars doesn't support H1.
    assert captured_params.get("timeframe") == "TIME_FRAME_M15"


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
