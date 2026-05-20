import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from starlette.websockets import WebSocketDisconnect

from trader.md.feed import MarketDataFeed
from trader.pos.models import Position

log = structlog.get_logger()

_SERVICES_INITIAL = ["auth", "tx", "oms", "pos", "audit"]


class WsHub:
    def __init__(
        self,
        feed: MarketDataFeed,
        pos_client=None,
        mvp_symbol: str = "",
        bars_stream=None,
        book_stream=None,
        base_url: str = "",
        get_token: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self._feed = feed
        self._pos_client = pos_client
        self._mvp_symbol = mvp_symbol
        self._bars_stream = bars_stream
        self._book_stream = book_stream
        self._base_url = base_url
        self._get_token = get_token
        self._clients: dict[object, asyncio.Queue] = {}
        self._broadcast_tasks: list[asyncio.Task] = []
        self._pos_poll_task: asyncio.Task | None = None
        self._bars_task: asyncio.Task | None = None
        self._book_task: asyncio.Task | None = None
        self._bars_history: list[dict] = []

    async def start(self, symbols: list[str]) -> None:
        for symbol in symbols:
            await self._feed.add_symbol(symbol)
            task = asyncio.create_task(self._broadcast_loop(symbol))
            self._broadcast_tasks.append(task)

        if self._pos_client:
            self._pos_poll_task = asyncio.create_task(self._pos_poll_loop())

        if symbols:
            symbol = symbols[0]
            if self._base_url and self._get_token:
                self._bars_history = await self._fetch_history(symbol)
            if self._bars_stream:
                await self._bars_stream.subscribe(symbol)
                self._bars_task = asyncio.create_task(self._bars_broadcast_loop(symbol))
            if self._book_stream:
                await self._book_stream.subscribe(symbol)
                self._book_task = asyncio.create_task(self._book_broadcast_loop(symbol))

    async def stop(self) -> None:
        for task in self._broadcast_tasks:
            task.cancel()
        if self._pos_poll_task:
            self._pos_poll_task.cancel()
        if self._bars_task:
            self._bars_task.cancel()
        if self._book_task:
            self._book_task.cancel()
        if self._bars_stream:
            await self._bars_stream.close()
        if self._book_stream:
            await self._book_stream.close()

    async def connect(self, websocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._clients[websocket] = queue

        if self._mvp_symbol:
            self._put(queue, {
                "type": "robot_update",
                "robots": [{
                    "id": "mvp",
                    "name": self._mvp_symbol,
                    "symbol": self._mvp_symbol,
                    "deposit": 0,
                    "pnl": 0,
                    "tradeCount": 0,
                    "position": 0,
                }],
            })

        for svc in _SERVICES_INITIAL:
            self._put(queue, {"type": "service_status", "service": svc, "status": "ok"})

        if self._bars_history:
            self._put(queue, {
                "type": "ohlc_history",
                "symbol": self._mvp_symbol,
                "bars": self._bars_history,
            })

        sender = asyncio.create_task(self._sender(websocket, queue))
        try:
            async for _ in websocket.iter_text():
                pass
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            sender.cancel()
            self._clients.pop(websocket, None)

    def _put(self, queue: asyncio.Queue, msg: dict) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(msg)

    async def _broadcast(self, msg: dict) -> None:
        for queue in list(self._clients.values()):
            self._put(queue, msg)

    async def _broadcast_loop(self, symbol: str) -> None:
        try:
            async for quote in self._feed.subscribe(symbol):
                msg = {
                    "type": "quote",
                    "symbol": quote.symbol,
                    "bid": float(quote.bid),
                    "ask": float(quote.ask),
                    "last": float(quote.last),
                    "bid_size": quote.bid_size,
                    "ask_size": quote.ask_size,
                    "last_size": quote.last_size,
                    "timestamp": quote.timestamp.isoformat().replace("+00:00", "Z"),
                }
                await self._broadcast(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("ws_hub.broadcast_crashed", exc=str(exc))
            await self._broadcast(
                {"type": "service_status", "service": "md", "status": "error"}
            )

    async def _pos_poll_loop(self, poll_interval: float = 5.0) -> None:
        while True:
            await asyncio.sleep(poll_interval)
            try:
                positions: list[Position] = await self._pos_client.get_portfolio()
                await self._broadcast({
                    "type": "position_update",
                    "positions": [p.model_dump(mode="json") for p in positions],
                })
                summary = await self._pos_client.get_account_summary()
                await self._broadcast({
                    "type": "account",
                    "deposit": float(summary.deposit),
                    "free": float(summary.free),
                    "in_position": float(summary.in_position),
                    "variation_margin": float(summary.variation_margin),
                })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws_hub.pos_poll_error", exc=str(exc))

    async def _bars_broadcast_loop(self, symbol: str) -> None:
        try:
            async for bar in self._bars_stream.iter_bars():
                if self._bars_history and self._bars_history[-1]["time"] == bar["time"]:
                    self._bars_history[-1] = bar
                else:
                    self._bars_history.append(bar)
                    if len(self._bars_history) > 500:
                        self._bars_history.pop(0)
                await self._broadcast({
                    "type": "ohlc_update",
                    "symbol": symbol,
                    "time": bar["time"],
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("ws_hub.bars_crashed", exc=str(exc))

    async def _book_broadcast_loop(self, symbol: str) -> None:
        try:
            async for snapshot in self._book_stream.iter_books():
                await self._broadcast({
                    "type": "orderbook",
                    "symbol": snapshot["symbol"],
                    "bids": snapshot["bids"],
                    "asks": snapshot["asks"],
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("ws_hub.book_crashed", exc=str(exc))

    async def _fetch_history(self, symbol: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=30)
        params = {
            "timeframe": "TIME_FRAME_M5",
            "interval.start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval.end_time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            token = await self._get_token(False)
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(http2=True) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/instruments/{symbol}/bars",
                    params=params,
                    headers=headers,
                    timeout=15.0,
                )
                resp.raise_for_status()
                body = resp.json()

                def flt(v) -> float:
                    if isinstance(v, dict):
                        return float(v.get("value", 0) or 0)
                    return float(v or 0)

                result = []
                for b in body.get("bars", []):
                    ts_str = b.get("timestamp", "")
                    if not ts_str:
                        continue
                    t = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    result.append({
                        "time": int(t.timestamp()),
                        "open": flt(b.get("open")),
                        "high": flt(b.get("high")),
                        "low": flt(b.get("low")),
                        "close": flt(b.get("close")),
                        "volume": flt(b.get("volume")),
                    })
                log.info("ws_hub.history_loaded", symbol=symbol, count=len(result))
                return result[-500:]
        except Exception as exc:
            log.warning("ws_hub.fetch_history_error", exc=str(exc))
            return []

    async def _sender(self, websocket, queue: asyncio.Queue) -> None:
        try:
            while True:
                msg = await queue.get()
                await websocket.send_json(msg)
        except Exception:
            pass
