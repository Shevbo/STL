"""Broker / exchange interface — the STRICT contract robots trade through.

Robots and strategies NEVER talk to a concrete broker (Finam API, QUIK, ...). They
depend only on ``BrokerInterface`` here; the concrete adapter is chosen at runtime from
settings (``exchange_interface``) via ``trader.broker.registry``. This keeps STL broker-,
exchange- and interface-agnostic, and lets each adapter ship as an independent pluggable
product that drops into any STL installation.

Design rules:
- No hardcoded broker choice anywhere outside the registry/factory.
- Every adapter declares ``capabilities()``; it overrides only the functions it supports.
  Unsupported functions raise ``UnsupportedCapability`` (never silently no-op). Robots
  check capabilities and degrade gracefully.
- Async-first (STL runs on asyncio). Streaming uses async callbacks / subscriptions.
- Adapter-specific config (endpoints, account, tokens) comes from settings/keymaster BY
  NAME — never embedded in the adapter.

The capability set maps the operator's required robot-trading functions:
  1 instrument params   -> instrument()           CAP.INSTRUMENTS
  2 order books         -> order_book()/subscribe CAP.ORDER_BOOK[_STREAM]
  3 place into book     -> place_order()           CAP.PLACE_ORDER
  4 order-book control  -> order_book()+health     CAP.ORDER_BOOK
  5 position control    -> positions()/reconcile   CAP.POSITIONS
  6 order control       -> orders()/subscribe      CAP.ORDERS[_STREAM]
  7 margin/free cash    -> account()               CAP.ACCOUNT
  8 broker news         -> news()/subscribe        CAP.NEWS
  9 connection state    -> connection()            CAP.CONNECTION
  10 trader messages    -> messages()/subscribe    CAP.MESSAGES
  (+ cancel/replace/maker-execution for full order lifecycle)
"""

from __future__ import annotations

import enum
from abc import ABC
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class Capability(str, enum.Enum):
    INSTRUMENTS = "instruments"
    ORDER_BOOK = "order_book"
    ORDER_BOOK_STREAM = "order_book_stream"
    QUOTE = "quote"
    PLACE_ORDER = "place_order"
    CANCEL_ORDER = "cancel_order"
    REPLACE_ORDER = "replace_order"
    MAKER_EXECUTION = "maker_execution"  # passive working near the touch (the 1b loop)
    ORDERS = "orders"
    ORDER_STREAM = "order_stream"
    POSITIONS = "positions"
    ACCOUNT = "account"
    CONNECTION = "connection"
    NEWS = "news"
    MESSAGES = "messages"


class UnsupportedCapability(NotImplementedError):
    """Raised when a robot calls a function the active adapter does not support."""

    def __init__(self, cap: Capability) -> None:
        super().__init__(f"adapter does not support capability {cap.value!r}")
        self.capability = cap


# ---- capability tiers -------------------------------------------------------------
# CORE: the minimum a robot needs to TRADE SAFELY. An adapter missing ANY of these is
# not trade-ready and the registry must refuse to hand it to a robot for live trading.
# Rationale per item: you cannot size/price without instrument params; cannot decide or
# route without the book; cannot trade without place; MUST be able to cancel (risk);
# must see order status, real exchange position (over-position guard), margin/free funds
# (over-leverage guard — the runaway hit a [GW] margin limit), and the link state (never
# trade on a dead connection).
CORE_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.INSTRUMENTS,
    Capability.ORDER_BOOK,
    Capability.PLACE_ORDER,
    Capability.CANCEL_ORDER,
    Capability.REPLACE_ORDER,  # native atomic move — see replace_order(): right & fast
    Capability.ORDERS,
    Capability.POSITIONS,
    Capability.ACCOUNT,
    Capability.CONNECTION,
})

# SECOND_WAVE: enhances trading but is emulatable or non-blocking. Streams are pushes
# over polling; quote derives from the book; maker-execution is advanced passive working
# (built ON replace_order); news/messages are informational.
SECOND_WAVE_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.ORDER_BOOK_STREAM,
    Capability.QUOTE,
    Capability.MAKER_EXECUTION,
    Capability.ORDER_STREAM,
    Capability.NEWS,
    Capability.MESSAGES,
})


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderState(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class LinkState(str, enum.Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


# ---- data models (broker-neutral) ----

@dataclass(frozen=True)
class Instrument:
    """[1] Instrument reference. price_step/step_cost drive commission and tick math."""
    symbol: str
    class_code: str = ""
    name: str = ""
    price_step: float = 0.0
    step_cost: float = 0.0       # cost of one price step (RUB) -> coef = step_cost/price_step
    lot_size: int = 1
    min_price: float = 0.0       # lower price limit (планка), 0 = unknown
    max_price: float = 0.0       # upper price limit (планка), 0 = unknown
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BookLevel:
    price: float
    qty: int


@dataclass(frozen=True)
class OrderBook:
    """[2,4] Order book snapshot. bids/asks sorted best-first. ts = source time (ms)."""
    symbol: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    ts_ms: int = 0

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True)
class Tick:
    symbol: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    open_interest: int = 0
    ts_ms: int = 0


@dataclass(frozen=True)
class OrderRequest:
    """[3] A robot's order intent. price is ignored for MARKET. client_id correlates."""
    symbol: str
    side: Side
    qty: int
    price: float = 0.0
    order_type: OrderType = OrderType.LIMIT
    client_id: str = ""


@dataclass(frozen=True)
class OrderRef:
    client_id: str
    order_id: str = ""   # broker/exchange id, filled once known


@dataclass(frozen=True)
class Order:
    """[6] Order state as reported by the broker."""
    ref: OrderRef
    symbol: str
    side: Side
    qty: int
    filled: int
    price: float
    state: OrderState
    text: str = ""
    ts_ms: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.qty - self.filled)


@dataclass(frozen=True)
class Position:
    """[5] Net position at the exchange. qty signed (long > 0, short < 0)."""
    symbol: str
    qty: int
    avg_price: float = 0.0
    ts_ms: int = 0


@dataclass(frozen=True)
class PositionDiff:
    """[5] Reconciliation of an exchange position against the robot's expectation."""
    symbol: str
    exchange_qty: int
    robot_qty: int

    @property
    def delta(self) -> int:
        return self.exchange_qty - self.robot_qty

    @property
    def matched(self) -> bool:
        return self.delta == 0


@dataclass(frozen=True)
class Account:
    """[7] Margin / free funds snapshot. margin_used = ГО, free = свободные средства."""
    account_id: str = ""
    equity: float = 0.0
    margin_used: float = 0.0     # ГО (guarantee)
    free: float = 0.0            # свободные средства
    variation_margin: float = 0.0
    currency: str = "RUB"
    ts_ms: int = 0


@dataclass(frozen=True)
class ConnState:
    """[9] Connection health: broker session, exchange, and the agent link if any."""
    broker_up: bool
    exchange_up: bool
    link: LinkState = LinkState.UP
    last_seen_ms: int = 0
    detail: str = ""


@dataclass(frozen=True)
class News:
    """[8] A broker/exchange news item."""
    id: str
    ts_ms: int
    title: str
    body: str = ""
    source: str = ""


@dataclass(frozen=True)
class TraderMessage:
    """[10] A message addressed to the trader by the broker/terminal."""
    id: str
    ts_ms: int
    text: str
    severity: str = "info"


# Subscription callbacks: async callables invoked per event. Subscriptions return an
# unsubscribe coroutine factory.
OrderBookHandler = Callable[[OrderBook], Awaitable[None]]
OrderHandler = Callable[[Order], Awaitable[None]]
NewsHandler = Callable[[News], Awaitable[None]]
MessageHandler = Callable[[TraderMessage], Awaitable[None]]
Unsubscribe = Callable[[], Awaitable[None]]


def reconcile_position(exchange: Position | None, robot_qty: int, symbol: str) -> PositionDiff:
    """[5] Free helper: compare an exchange position to the robot's expected qty."""
    ex_qty = exchange.qty if exchange is not None else 0
    return PositionDiff(symbol=symbol, exchange_qty=ex_qty, robot_qty=robot_qty)


class BrokerInterface(ABC):
    """The strict contract. An adapter overrides the functions it supports and lists them
    in ``capabilities()``. Everything else raises ``UnsupportedCapability`` by default, so
    a robot can rely on the full surface and check ``supports()`` before optional calls.
    """

    #: stable adapter name, e.g. "finam", "quik". Set by the subclass.
    name: str = "base"

    # ---- lifecycle ----
    async def connect(self) -> None:  # noqa: B027 - optional hook
        """Establish the broker session. No-op by default."""

    async def disconnect(self) -> None:  # noqa: B027 - optional hook
        """Tear down the broker session. No-op by default."""

    def capabilities(self) -> set[Capability]:
        """The functions this adapter actually supports. Override in the adapter."""
        return set()

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities()

    def missing_core(self) -> set[Capability]:
        """CORE capabilities this adapter does NOT provide. Empty -> trade-ready."""
        return set(CORE_CAPABILITIES) - self.capabilities()

    def is_trade_ready(self) -> bool:
        """True iff every CORE capability is supported. The registry gates live trading
        on this so a partial adapter can never be used to place real orders."""
        return not self.missing_core()

    def _require(self, cap: Capability) -> None:
        if not self.supports(cap):
            raise UnsupportedCapability(cap)

    # ---- [1] instruments ----
    async def instrument(self, symbol: str) -> Instrument:
        self._require(Capability.INSTRUMENTS)
        raise UnsupportedCapability(Capability.INSTRUMENTS)

    async def instruments(self) -> list[Instrument]:
        self._require(Capability.INSTRUMENTS)
        raise UnsupportedCapability(Capability.INSTRUMENTS)

    # ---- [2,4] order book + quote ----
    async def order_book(self, symbol: str, depth: int = 10) -> OrderBook:
        self._require(Capability.ORDER_BOOK)
        raise UnsupportedCapability(Capability.ORDER_BOOK)

    async def subscribe_order_book(self, symbol: str, handler: OrderBookHandler) -> Unsubscribe:
        self._require(Capability.ORDER_BOOK_STREAM)
        raise UnsupportedCapability(Capability.ORDER_BOOK_STREAM)

    async def quote(self, symbol: str) -> Tick:
        self._require(Capability.QUOTE)
        raise UnsupportedCapability(Capability.QUOTE)

    # ---- [3] orders (write) ----
    async def place_order(self, req: OrderRequest) -> OrderRef:
        self._require(Capability.PLACE_ORDER)
        raise UnsupportedCapability(Capability.PLACE_ORDER)

    async def cancel_order(self, ref: OrderRef) -> None:
        self._require(Capability.CANCEL_ORDER)
        raise UnsupportedCapability(Capability.CANCEL_ORDER)

    async def replace_order(self, ref: OrderRef, new_price: float, new_qty: int | None = None) -> OrderRef:
        """CORE. Move a resting order to a new price/qty in ONE atomic broker transaction
        (QUIK ACTION=MOVE_ORDERS; Finam replace). It MUST be native+atomic where the
        broker supports it: never an internal cancel-then-place — that opens a window of
        zero or two live orders and is the class of bug behind the maker-loop runaway.
        Right and fast: one round-trip, no cancel-confirm wait. Returns the (possibly new)
        OrderRef. Adapters without a native move must NOT claim REPLACE_ORDER capability."""
        self._require(Capability.REPLACE_ORDER)
        raise UnsupportedCapability(Capability.REPLACE_ORDER)

    async def start_execution(self, req: OrderRequest, worst_price: float, allow_cross: bool = False) -> OrderRef:
        """Maker-working an order near the touch (the 1b loop), never crossing by default."""
        self._require(Capability.MAKER_EXECUTION)
        raise UnsupportedCapability(Capability.MAKER_EXECUTION)

    async def stop_execution(self, ref: OrderRef) -> None:
        self._require(Capability.MAKER_EXECUTION)
        raise UnsupportedCapability(Capability.MAKER_EXECUTION)

    # ---- [6] orders (read) ----
    async def orders(self) -> list[Order]:
        self._require(Capability.ORDERS)
        raise UnsupportedCapability(Capability.ORDERS)

    async def order(self, ref: OrderRef) -> Order | None:
        self._require(Capability.ORDERS)
        raise UnsupportedCapability(Capability.ORDERS)

    async def subscribe_orders(self, handler: OrderHandler) -> Unsubscribe:
        self._require(Capability.ORDER_STREAM)
        raise UnsupportedCapability(Capability.ORDER_STREAM)

    # ---- [5] positions ----
    async def positions(self) -> list[Position]:
        self._require(Capability.POSITIONS)
        raise UnsupportedCapability(Capability.POSITIONS)

    async def position(self, symbol: str) -> Position | None:
        self._require(Capability.POSITIONS)
        raise UnsupportedCapability(Capability.POSITIONS)

    async def reconcile(self, symbol: str, robot_qty: int) -> PositionDiff:
        """[5] Compare the exchange position to the robot's expected qty."""
        pos = await self.position(symbol)
        return reconcile_position(pos, robot_qty, symbol)

    # ---- [7] account / margin ----
    async def account(self) -> Account:
        self._require(Capability.ACCOUNT)
        raise UnsupportedCapability(Capability.ACCOUNT)

    # ---- [9] connection ----
    async def connection(self) -> ConnState:
        self._require(Capability.CONNECTION)
        raise UnsupportedCapability(Capability.CONNECTION)

    # ---- [8] news ----
    async def news(self, limit: int = 50) -> list[News]:
        self._require(Capability.NEWS)
        raise UnsupportedCapability(Capability.NEWS)

    async def subscribe_news(self, handler: NewsHandler) -> Unsubscribe:
        self._require(Capability.NEWS)
        raise UnsupportedCapability(Capability.NEWS)

    # ---- [10] trader messages ----
    async def messages(self, limit: int = 50) -> list[TraderMessage]:
        self._require(Capability.MESSAGES)
        raise UnsupportedCapability(Capability.MESSAGES)

    async def subscribe_messages(self, handler: MessageHandler) -> Unsubscribe:
        self._require(Capability.MESSAGES)
        raise UnsupportedCapability(Capability.MESSAGES)

    # ---- streaming convenience ----
    async def stream_order_books(self, symbol: str) -> AsyncIterator[OrderBook]:  # pragma: no cover
        """Optional async-generator form of subscribe_order_book for adapters that prefer it."""
        raise UnsupportedCapability(Capability.ORDER_BOOK_STREAM)
        yield  # type: ignore[unreachable]
