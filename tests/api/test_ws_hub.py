import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect

from trader.api.ws_hub import WsHub
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
