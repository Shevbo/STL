"""FinamBroker — BrokerInterface adapter over the existing Finam Trade API clients.

Wraps the clients STL already ships:
  * ``trader.auth.AsyncAuthClient``      — session token (Bearer) + account id
  * ``trader.registry.InstrumentRegistry`` — instrument reference / params  [1]
  * ``trader.md`` gRPC MarketData unary   — order book + last quote          [2,4]
  * ``trader.tx.TxClient``                — place limit order                 [3]
  * ``trader.pos.PositionsClient``        — positions + account/margin        [5,7]

Honest capability claim (see ``capabilities()``): the existing Finam clients do
NOT yet expose order CANCEL, native atomic REPLACE, or a read-orders list. Per
the contract those CORE caps are therefore NOT claimed, so FinamBroker is
correctly **not trade-ready** until the tx client grows cancel / orders / native
replace. ``missing_core()`` surfaces exactly that. We never emulate replace as
cancel+place (the maker-loop runaway class).

Construction is from ``settings`` only (secrets by env NAME). Nothing here sends
an order at import or in tests; clients are built lazily and orders only flow
through ``place_order()``.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from trader.broker.base import (
    Account,
    BookLevel,
    BrokerInterface,
    Capability,
    ConnState,
    Instrument,
    LinkState,
    OrderBook,
    OrderRef,
    OrderRequest,
    OrderType,
    Position,
    Tick,
)
from trader.broker.registry import register

log = structlog.get_logger()

_GRPC_TARGET = "api.finam.ru:443"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _f(v: Any, default: float = 0.0) -> float:
    """Best-effort float from a proto Decimal-ish (.value) or a scalar."""
    if v is None:
        return default
    inner = getattr(v, "value", v)
    try:
        return float(inner or default)
    except (TypeError, ValueError):
        return default


class FinamBroker(BrokerInterface):
    """Finam Trade API adapter. CORE it can today: instruments, order_book, quote,
    place_order, positions, account, connection. It does NOT claim cancel_order,
    replace_order or orders (the wrapped tx client lacks them) -> not trade-ready."""

    name = "finam"

    def __init__(
        self,
        settings: Any,
        *,
        auth: Any = None,
        registry: Any = None,
        tx: Any = None,
        pos: Any = None,
        channel_factory: Any = None,
    ) -> None:
        """Build from settings. Dependencies may be injected (tests pass fakes);
        otherwise they are created lazily on first use from settings BY NAME."""
        self._settings = settings
        self._base_url = getattr(settings, "finam_api_base_url", "https://api.finam.ru")
        self._account_id = getattr(settings, "finam_account_id", "") or ""
        self._auth = auth
        self._registry = registry
        self._tx = tx
        self._pos = pos
        # channel_factory() -> grpc.aio.Channel (overridable in tests). Default
        # builds a TLS channel to Finam lazily; constructing the broker never dials.
        self._channel_factory = channel_factory
        self._channel = None
        self._md_stub = None

    # ---- capabilities ----
    def capabilities(self) -> set[Capability]:
        # Claimed honestly against what the wrapped clients actually provide.
        return {
            Capability.INSTRUMENTS,   # InstrumentRegistry
            Capability.ORDER_BOOK,    # MarketData OrderBook (unary)
            Capability.QUOTE,         # MarketData LastQuote (second wave)
            Capability.PLACE_ORDER,   # TxClient.place_order (limit only)
            Capability.POSITIONS,     # PositionsClient.get_portfolio
            Capability.ACCOUNT,       # PositionsClient.get_account_summary
            Capability.CONNECTION,    # token + channel health
            # NOT claimed (wrapped clients lack them -> not trade-ready):
            #   CANCEL_ORDER, REPLACE_ORDER, ORDERS.
        }

    # ---- lazy dependency builders (no network at construct) ----
    def _ensure_auth(self):
        if self._auth is None:
            from trader.auth.client import AsyncAuthClient

            token = self._settings.finam_secret_token.get_secret_value()
            self._auth = AsyncAuthClient(
                base_url=self._base_url,
                secret_token=token,
                refresh_before_secs=getattr(
                    self._settings, "finam_token_refresh_before_secs", 60
                ),
            )
        return self._auth

    def _ensure_registry(self):
        if self._registry is None:
            from trader.registry.client import InstrumentRegistry

            self._registry = InstrumentRegistry(
                base_url=self._base_url, get_token=self._ensure_auth().get_token
            )
        return self._registry

    def _ensure_tx(self):
        if self._tx is None:
            from trader.tx.client import TxClient

            self._tx = TxClient(
                base_url=self._base_url,
                get_token=self._ensure_auth().get_token,
                account_id=self._account_id,
            )
        return self._tx

    def _ensure_pos(self):
        if self._pos is None:
            from trader.pos.client import PositionsClient

            self._pos = PositionsClient(
                base_url=self._base_url,
                get_token=self._ensure_auth().get_token,
                account_id=self._account_id,
            )
        return self._pos

    def _ensure_md_stub(self):
        if self._md_stub is not None:
            return self._md_stub
        if self._channel_factory is not None:
            self._channel = self._channel_factory()
        else:
            import grpc
            import grpc.aio

            # Bootstrap generated stubs as grpc.tradeapi.* (same as md.grpc_client).
            import trader.md.grpc_client  # noqa: F401

            creds = grpc.ssl_channel_credentials()
            self._channel = grpc.aio.secure_channel(_GRPC_TARGET, creds)
        from grpc.tradeapi.v1.marketdata.marketdata_service_pb2_grpc import (
            MarketDataServiceStub,
        )

        self._md_stub = MarketDataServiceStub(self._channel)
        return self._md_stub

    async def _md_metadata(self) -> list[tuple[str, str]]:
        token = await self._ensure_auth().get_token()
        return [("authorization", f"Bearer {token}")]

    # ---- lifecycle ----
    async def disconnect(self) -> None:
        for client in (self._tx, self._pos, self._registry, self._auth):
            close = getattr(client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:  # pragma: no cover - cleanup best-effort
                    log.warning("finam.aclose_failed", error=str(exc))
        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception as exc:  # pragma: no cover
                log.warning("finam.channel_close_failed", error=str(exc))

    # ---- [1] instruments ----
    async def instrument(self, symbol: str) -> Instrument:
        self._require(Capability.INSTRUMENTS)
        reg = self._ensure_registry()
        detail = await reg.get_detail(symbol, self._account_id)
        step = float(getattr(detail, "min_step", 0) or 0)
        # step_cost is not in the detail; params carry margins, not tick value.
        return Instrument(
            symbol=getattr(detail, "symbol", symbol),
            class_code=getattr(detail, "mic", "") or "",
            name=getattr(detail, "name", "") or "",
            price_step=step,
            step_cost=0.0,
            lot_size=int(getattr(detail, "lot_size", 1) or 1),
            extra={
                "ticker": getattr(detail, "ticker", ""),
                "type": getattr(detail, "type", ""),
                "quote_currency": getattr(detail, "quote_currency", ""),
            },
        )

    async def instruments(self) -> list[Instrument]:
        self._require(Capability.INSTRUMENTS)
        reg = self._ensure_registry()
        # InstrumentRegistry.search loads the full cache; expose it broker-neutral.
        if reg._cache is None:  # noqa: SLF001 - private cache is the only list source
            reg._cache = await reg._load_all()  # noqa: SLF001
        out: list[Instrument] = []
        for inst in reg._cache.values():  # noqa: SLF001
            out.append(
                Instrument(
                    symbol=inst.symbol,
                    class_code=getattr(inst, "mic", "") or "",
                    name=getattr(inst, "name", "") or "",
                    extra={"ticker": getattr(inst, "ticker", "")},
                )
            )
        return out

    # ---- [2,4] order book + quote ----
    async def order_book(self, symbol: str, depth: int = 10) -> OrderBook:
        self._require(Capability.ORDER_BOOK)
        from grpc.tradeapi.v1.marketdata.marketdata_service_pb2 import OrderBookRequest

        stub = self._ensure_md_stub()
        resp = await stub.OrderBook(
            OrderBookRequest(symbol=symbol), metadata=await self._md_metadata()
        )
        bids: list[BookLevel] = []
        asks: list[BookLevel] = []
        for row in resp.orderbook.rows:
            price = _f(getattr(row, "price", None))
            field = row.WhichOneof("side") if hasattr(row, "WhichOneof") else None
            if field == "buy_size":
                bids.append(BookLevel(price=price, qty=int(_f(row.buy_size))))
            elif field == "sell_size":
                asks.append(BookLevel(price=price, qty=int(_f(row.sell_size))))
        bids.sort(key=lambda lv: lv.price, reverse=True)
        asks.sort(key=lambda lv: lv.price)
        return OrderBook(
            symbol=symbol, bids=bids[:depth], asks=asks[:depth], ts_ms=_now_ms()
        )

    async def quote(self, symbol: str) -> Tick:
        self._require(Capability.QUOTE)
        from grpc.tradeapi.v1.marketdata.marketdata_service_pb2 import QuoteRequest

        stub = self._ensure_md_stub()
        resp = await stub.LastQuote(
            QuoteRequest(symbol=symbol), metadata=await self._md_metadata()
        )
        q = resp.quote
        return Tick(
            symbol=symbol,
            last=_f(getattr(q, "last", None)),
            bid=_f(getattr(q, "bid", None)),
            ask=_f(getattr(q, "ask", None)),
            ts_ms=_now_ms(),
        )

    # ---- [3] orders (write) ----
    async def place_order(self, req: OrderRequest) -> OrderRef:
        self._require(Capability.PLACE_ORDER)
        if req.order_type is not OrderType.LIMIT:
            raise ValueError("FinamBroker accepts LIMIT orders only (no market orders)")
        from trader.tx.models import OrderRequest as FinamOrderRequest

        tx = self._ensure_tx()
        finam_req = FinamOrderRequest(
            symbol=req.symbol,
            side=req.side.value,
            quantity=int(req.qty),
            order_type="limit",
            price=req.price,
            **({"client_order_id": req.client_id} if req.client_id else {}),
        )
        resp = await tx.place_order(finam_req)
        return OrderRef(client_id=finam_req.client_order_id, order_id=resp.order_id)

    # ---- [5] positions ----
    async def positions(self) -> list[Position]:
        self._require(Capability.POSITIONS)
        pos = self._ensure_pos()
        out: list[Position] = []
        for p in await pos.get_portfolio():
            qty = int(p.quantity)
            if p.side == "short":
                qty = -qty
            elif p.side == "flat":
                qty = 0
            out.append(
                Position(
                    symbol=p.symbol,
                    qty=qty,
                    avg_price=float(p.avg_price),
                    ts_ms=_now_ms(),
                )
            )
        return out

    async def position(self, symbol: str) -> Position | None:
        self._require(Capability.POSITIONS)
        for p in await self.positions():
            if p.symbol == symbol:
                return p
        return None

    # ---- [7] account / margin ----
    async def account(self) -> Account:
        self._require(Capability.ACCOUNT)
        pos = self._ensure_pos()
        summary = await pos.get_account_summary()
        return Account(
            account_id=self._account_id,
            equity=float(summary.deposit),
            margin_used=float(summary.in_position),
            free=float(summary.free),
            variation_margin=float(summary.variation_margin),
            ts_ms=_now_ms(),
        )

    # ---- [9] connection ----
    async def connection(self) -> ConnState:
        self._require(Capability.CONNECTION)
        broker_up = False
        detail = ""
        try:
            token = await self._ensure_auth().get_token()
            broker_up = bool(token)
        except Exception as exc:  # pragma: no cover - exercised against real API
            detail = f"auth error: {exc}"
        return ConnState(
            broker_up=broker_up,
            exchange_up=broker_up,  # Finam fronts the exchange; no separate lamp.
            link=LinkState.UP if broker_up else LinkState.DOWN,
            last_seen_ms=_now_ms(),
            detail=detail,
        )


@register("finam")
def _build_finam(settings: Any, **_inject: Any) -> FinamBroker:
    """Registry factory: build a FinamBroker from settings (config by NAME). The Finam
    adapter needs no injected deps (it builds from settings), so any injection bag passed
    by the caller (e.g. the QUIK stores) is accepted and ignored."""
    return FinamBroker(settings)
