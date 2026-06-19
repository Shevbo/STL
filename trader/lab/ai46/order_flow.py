"""Order-flow collector for team-46 — live trades + order book → OFI family.

This is the "точнее" path: real order flow, not a proxy from 1-min bars.
`OrderFlow` consumes parsed trades and order-book snapshots and exposes the
OFI family (features.ofi/mlofi/queue_imbalance/microprice/spread_bps) per symbol.
`TradesStream` is the thin Finam gRPC transport (SubscribeLatestTrades) that
feeds it; the collector itself is pure and unit-testable without gRPC.

Finam's public market-data feed is delayed (~15 min) and side may be
SIDE_UNSPECIFIED, so trades are aged on the DATA clock (newest trade time) and
an unspecified side is inferred with the classic tick rule.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from trader.lab.ai46 import features as F

# Side enum int values from the marketdata proto (Trade.side).
_SIDE_BUY = 1   # SIDE_BUY
_SIDE_SELL = 2  # SIDE_SELL

_OFI_WINDOW_SECS = 300.0  # 5-minute OFI window (matches the go-bot strategy)


class OrderFlow:
    """Per-symbol order-flow state: a TradeBuffer + the latest order book."""

    def __init__(self, max_age_secs: float = 1800.0) -> None:
        self._tb = F.TradeBuffer(max_age_secs=max_age_secs)
        self._books: dict[str, F.OrderBook] = {}
        self._last_price: dict[str, float] = {}
        self._last_side: dict[str, str] = {}

    # ── ingestion ────────────────────────────────────────────────────────────

    def on_trade(self, symbol: str, time: float, price: float, size: float,
                 side_enum: int = 0) -> None:
        """Add one trade. side_enum 1=buy, 2=sell, else inferred via tick rule."""
        if side_enum == _SIDE_BUY:
            side = "buy"
        elif side_enum == _SIDE_SELL:
            side = "sell"
        else:
            last = self._last_price.get(symbol)
            if last is None or price > last:
                side = "buy"
            elif price < last:
                side = "sell"
            else:
                side = self._last_side.get(symbol, "buy")
        self._last_price[symbol] = price
        self._last_side[symbol] = side
        # Age on the data clock (newest trade time), not wall-clock — the feed lags.
        self._tb.add(symbol, F.Trade(time=time, side=side, volume=size), now=time)

    def on_book(self, symbol: str, bids: list[tuple[float, float]],
                asks: list[tuple[float, float]]) -> None:
        """Replace the order book. bids/asks are (price, size), best first."""
        self._books[symbol] = F.OrderBook(
            bids=[F.BookLevel(p, s) for p, s in bids],
            asks=[F.BookLevel(p, s) for p, s in asks],
        )

    # ── features ───────────────────────────────────────────────────────────-

    def ofi(self, symbol: str, window_secs: float = _OFI_WINDOW_SECS) -> float:
        return F.ofi(self._tb, symbol, window_secs)

    def mlofi(self, symbol: str) -> float:
        return F.mlofi(self._books.get(symbol))

    def queue_imbalance(self, symbol: str) -> float:
        return F.queue_imbalance(self._books.get(symbol))

    def microprice(self, symbol: str) -> float:
        return F.microprice(self._books.get(symbol))

    def spread_bps(self, symbol: str) -> float:
        return F.spread_bps(self._books.get(symbol))

    def book(self, symbol: str) -> F.OrderBook | None:
        return self._books.get(symbol)

    def stats(self, symbol: str, now: float) -> F.BufferStat:
        return self._tb.stats(symbol, now)

    def snapshot(self, symbol: str, window_secs: float = _OFI_WINDOW_SECS) -> dict:
        """All order-flow features for the LLM/ML proposal."""
        return {
            "ofi": self.ofi(symbol, window_secs),
            "mlofi": self.mlofi(symbol),
            "queue_imbalance": self.queue_imbalance(symbol),
            "microprice": self.microprice(symbol),
            "spread_bps": self.spread_bps(symbol),
        }


# ════════════════════════════════════════════════════════════════════════════
#  Live Finam gRPC transport (SubscribeLatestTrades)
# ════════════════════════════════════════════════════════════════════════════

def _trade_to_tuple(pb_trade) -> tuple[int, float, float, int]:
    """(unix_seconds, price, size, side_enum) from a proto Trade."""
    ts = int(pb_trade.timestamp.ToDatetime().timestamp())
    price = F.unwrap_decimal(pb_trade.price, as_float=True) if hasattr(pb_trade.price, "value") \
        else float(pb_trade.price or 0)
    size = F.unwrap_decimal(pb_trade.size, as_float=True) if hasattr(pb_trade.size, "value") \
        else float(pb_trade.size or 0)
    return ts, price, size, int(pb_trade.side)


class TradesStream:
    """Finam gRPC SubscribeLatestTrades → OrderFlow.on_trade, with reconnect.

    Mirrors trader.md.grpc_client.OrderBookStream. Importing trader.md.grpc_client
    bootstraps the generated `grpc.tradeapi.*` namespace before we import the stub.
    """

    def __init__(self, flow: OrderFlow) -> None:
        self._flow = flow
        self._channel = None
        self._get_token: Callable[[bool], Awaitable[str]] | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self, get_token: Callable[[bool], Awaitable[str]]) -> None:
        self._get_token = get_token
        self._running = True

    def _ensure_channel(self):
        import grpc
        import grpc.aio  # noqa: F401
        import trader.md.grpc_client as _gc  # bootstraps grpc.tradeapi namespace
        if self._channel is None:
            creds = grpc.ssl_channel_credentials()
            self._channel = grpc.aio.secure_channel(_gc.GRPC_TARGET, creds, options=_gc.CHANNEL_OPTIONS)

    async def subscribe(self, symbol: str) -> None:
        self._ensure_channel()
        if symbol not in self._tasks or self._tasks[symbol].done():
            self._tasks[symbol] = asyncio.create_task(self._stream_symbol(symbol))

    async def _stream_symbol(self, symbol: str) -> None:
        import grpc
        from grpc.tradeapi.v1.marketdata.marketdata_service_pb2_grpc import MarketDataServiceStub
        from grpc.tradeapi.v1.marketdata.marketdata_service_pb2 import SubscribeLatestTradesRequest
        from trader.md.grpc_client import _backoff, log
        attempt = 0
        while self._running:
            try:
                token = await self._get_token(attempt > 0)
                metadata = [("authorization", f"Bearer {token}")]
                stub = MarketDataServiceStub(self._channel)
                req = SubscribeLatestTradesRequest(symbol=symbol)
                async for resp in stub.SubscribeLatestTrades(req, metadata=metadata):
                    for pb_trade in resp.trades:
                        ts, price, size, side = _trade_to_tuple(pb_trade)
                        if size > 0:
                            self._flow.on_trade(symbol, ts, price, size, side)
                attempt = 0
            except grpc.aio.AioRpcError as exc:
                if not self._running:
                    return
                log.warning("trades.rpc_error", symbol=symbol, code=str(exc.code()), attempt=attempt)
                await asyncio.sleep(_backoff(attempt))
                attempt += 1
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                if not self._running:
                    return
                log.error("trades.stream_crashed", symbol=symbol, exc=str(exc))
                await asyncio.sleep(_backoff(attempt))
                attempt += 1

    async def close(self) -> None:
        self._running = False
        for t in self._tasks.values():
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        if self._channel:
            await self._channel.close()
