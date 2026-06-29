"""QuikBroker — BrokerInterface adapter over the in-process QUIK agent link.

It is an in-process adapter: rather than HTTP self-calls it talks DIRECTLY to the
shared app.state objects the FastAPI routes use:
  * ``quik_store``       (``QuikAgentStore``) — securities, order books, ticks,
                          link status (the agent stream's latest state).      [1,2,4,9]
  * ``quik_order_store`` (``OrderStore``)     — working orders / kill-switch.  [6]
  * ``quik_server``      (``QuikAgentServer``) — enqueue OrchestratorMessages. [3]

These are injected via the constructor (so the adapter is unit-testable with
fakes; no HTTP, no network). The same documented routes
(``/api/v1/quik/...``) remain the fallback surface.

Honest capability claim (``capabilities()``):
  * Claims CORE: INSTRUMENTS, ORDER_BOOK, PLACE_ORDER, CANCEL_ORDER, ORDERS,
    CONNECTION.
  * Does NOT claim POSITIONS or ACCOUNT — the agent does not report them yet.
  * Does NOT claim REPLACE_ORDER — native MOVE_ORDERS is sub-agent G's work: the
    generated pb has no ``replace_order`` field and there is no STL route/builder
    yet. We refuse to fake it as cancel+place (the maker-loop runaway class).
  => QuikBroker is therefore honestly **not trade-ready**; ``missing_core()``
     surfaces {positions, account, replace_order}. It becomes trade-ready once
     the agent reports positions/account and native replace lands.
  * Second wave: MAKER_EXECUTION maps to start/stop execution.
"""

from __future__ import annotations

import time
from typing import Any

from trader.broker.base import (
    BookLevel,
    BrokerInterface,
    Capability,
    ConnState,
    Instrument,
    LinkState,
    Order,
    OrderBook,
    OrderRef,
    OrderRequest,
    OrderState,
    OrderType,
    Side,
    Tick,
)
from trader.broker.registry import register

_STATE_MAP = {
    "pending": OrderState.PENDING,
    "active": OrderState.ACTIVE,
    "partial": OrderState.PARTIAL,
    "filled": OrderState.FILLED,
    "cancelled": OrderState.CANCELLED,
    "rejected": OrderState.REJECTED,
}
_WORKING_LINKS = {"green", "yellow"}


def _now_ms() -> int:
    return int(time.time() * 1000)


class QuikUnavailable(RuntimeError):
    """Raised when the QUIK agent link is not wired (store/server missing)."""


class QuikBroker(BrokerInterface):
    """QUIK agent adapter. Reads market/order state from the shared stores and
    enqueues operator order messages through the agent server. NOT trade-ready:
    no positions/account (agent silent) and no native replace yet."""

    name = "quik"

    def __init__(
        self,
        settings: Any,
        *,
        quik_store: Any = None,
        quik_order_store: Any = None,
        quik_server: Any = None,
        agent_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._store = quik_store
        self._order_store = quik_order_store
        self._server = quik_server
        # Pin a target agent, else resolve the single live/known one per call.
        self._agent_id = agent_id

    # ---- capabilities ----
    def capabilities(self) -> set[Capability]:
        return {
            Capability.INSTRUMENTS,    # store.securities / params
            Capability.ORDER_BOOK,     # store.order_book
            Capability.QUOTE,          # store.tick (second wave)
            Capability.PLACE_ORDER,    # build_place_order + enqueue
            Capability.CANCEL_ORDER,   # build_cancel_order + enqueue
            Capability.ORDERS,         # order_store.working_orders
            Capability.CONNECTION,     # store.status link lamp
            Capability.MAKER_EXECUTION,  # start/stop execution (second wave)
            # NOT claimed (agent does not report / sub-agent G not done):
            #   POSITIONS, ACCOUNT, REPLACE_ORDER  -> not trade-ready.
        }

    # ---- helpers ----
    def _need_store(self):
        if self._store is None:
            raise QuikUnavailable("QUIK agent store not wired (quik_agent_enabled=false)")
        return self._store

    def _need_order_store(self):
        if self._order_store is None:
            raise QuikUnavailable("QUIK order store not wired (quik_agent_enabled=false)")
        return self._order_store

    def _need_server(self):
        if self._server is None:
            raise QuikUnavailable("QUIK agent server not wired (quik_agent_enabled=false)")
        return self._server

    def _resolve_agent(self) -> str:
        """The pinned agent, else the single green agent, else the single known one."""
        if self._agent_id:
            return self._agent_id
        store = self._need_store()
        status = store.status()
        green = [r["agent_id"] for r in status if r.get("link") == "green"]
        if len(green) == 1:
            return green[0]
        ids = store.agent_ids()
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise QuikUnavailable("no QUIK agent connected")
        raise QuikUnavailable("multiple QUIK agents connected; pin agent_id")

    # ---- [1] instruments ----
    async def instrument(self, symbol: str) -> Instrument:
        self._require(Capability.INSTRUMENTS)
        store = self._need_store()
        for sec in store.securities(self._agent_id):
            if sec.get("code") == symbol:
                return self._instrument_from_sec(sec)
        raise KeyError(f"no QUIK security for {symbol!r}")

    async def instruments(self) -> list[Instrument]:
        self._require(Capability.INSTRUMENTS)
        store = self._need_store()
        return [self._instrument_from_sec(s) for s in store.securities(self._agent_id)]

    @staticmethod
    def _instrument_from_sec(sec: dict[str, Any]) -> Instrument:
        return Instrument(
            symbol=sec.get("code", ""),
            class_code=sec.get("class_code", "") or "",
            name=sec.get("name", "") or "",
            price_step=float(sec.get("price_step", 0) or 0),
            step_cost=float(sec.get("step_cost", 0) or 0),
            extra={k: v for k, v in sec.items() if k not in {"code", "name"}},
        )

    # ---- [2,4] order book + quote ----
    async def order_book(self, symbol: str, depth: int = 10) -> OrderBook:
        self._require(Capability.ORDER_BOOK)
        store = self._need_store()
        ob = store.order_book(symbol, self._agent_id)
        if ob is None:
            raise KeyError(f"no QUIK order book for {symbol!r}")
        bids = [
            BookLevel(price=float(lv["price"]), qty=int(lv.get("quantity", 0)))
            for lv in ob.get("bids", [])
        ]
        asks = [
            BookLevel(price=float(lv["price"]), qty=int(lv.get("quantity", 0)))
            for lv in ob.get("asks", [])
        ]
        bids.sort(key=lambda lv: lv.price, reverse=True)
        asks.sort(key=lambda lv: lv.price)
        return OrderBook(
            symbol=symbol,
            bids=bids[:depth],
            asks=asks[:depth],
            ts_ms=int(ob.get("received_at_unix_ms", 0) or _now_ms()),
        )

    async def quote(self, symbol: str) -> Tick:
        self._require(Capability.QUOTE)
        store = self._need_store()
        t = store.tick(symbol, self._agent_id)
        if t is None:
            raise KeyError(f"no QUIK tick for {symbol!r}")
        return Tick(
            symbol=symbol,
            last=float(t.get("last", 0) or 0),
            bid=float(t.get("bid", 0) or 0),
            ask=float(t.get("ask", 0) or 0),
            open_interest=int(t.get("open_interest", 0) or 0),
            ts_ms=int(t.get("exchange_ts_unix_ms", 0) or _now_ms()),
        )

    # ---- [3] orders (write) ----
    async def place_order(self, req: OrderRequest) -> OrderRef:
        self._require(Capability.PLACE_ORDER)
        if req.order_type is not OrderType.LIMIT:
            raise ValueError("QuikBroker accepts LIMIT orders only (no market orders)")
        from trader.quik import orders as order_msgs

        srv = self._need_server()
        agent = self._resolve_agent()
        collar = float(getattr(self._settings, "quik_price_collar_frac", 0.002) or 0.0)
        msg = order_msgs.build_place_order(
            client_id=req.client_id,
            code=req.symbol,
            side=req.side.value,
            price=float(req.price),
            quantity=int(req.qty),
            collar=collar,
        )
        # Mirror the route: register the pending order locally, then enqueue.
        if self._order_store is not None:
            self._order_store.register_pending(
                agent, req.client_id, req.symbol, req.side.value,
                float(req.price), int(req.qty),
            )
            self._order_store.record_placement(agent)
        srv.enqueue_order(agent, msg)
        return OrderRef(client_id=req.client_id)

    async def cancel_order(self, ref: OrderRef) -> None:
        self._require(Capability.CANCEL_ORDER)
        from trader.quik import orders as order_msgs

        srv = self._need_server()
        agent = self._resolve_agent()
        msg = order_msgs.build_cancel_order(ref.client_id, ref.order_id or "")
        srv.enqueue_order(agent, msg)

    # replace_order: NOT claimed. Native MOVE_ORDERS (pb.replace_order + STL route)
    # is sub-agent G's work; until then the base raises UnsupportedCapability and
    # we never emulate it as cancel+place.

    async def start_execution(
        self, req: OrderRequest, worst_price: float, allow_cross: bool = False
    ) -> OrderRef:
        self._require(Capability.MAKER_EXECUTION)
        from trader.quik import orders as order_msgs

        srv = self._need_server()
        agent = self._resolve_agent()
        msg = order_msgs.build_start_execution(
            client_id=req.client_id,
            code=req.symbol,
            side=req.side.value,
            target_quantity=int(req.qty),
            worst_price=float(worst_price),
            allow_cross=bool(allow_cross),
        )
        if self._order_store is not None:
            self._order_store.record_placement(agent)
        srv.enqueue_order(agent, msg)
        return OrderRef(client_id=req.client_id)

    async def stop_execution(self, ref: OrderRef) -> None:
        self._require(Capability.MAKER_EXECUTION)
        from trader.quik import orders as order_msgs

        srv = self._need_server()
        agent = self._resolve_agent()
        srv.enqueue_order(agent, order_msgs.build_stop_execution(ref.client_id))

    # ---- [6] orders (read) ----
    async def orders(self) -> list[Order]:
        self._require(Capability.ORDERS)
        ost = self._need_order_store()
        return [self._order_from_dict(d) for d in ost.working_orders(self._agent_id)]

    async def order(self, ref: OrderRef) -> Order | None:
        self._require(Capability.ORDERS)
        ost = self._need_order_store()
        for d in ost.working_orders(self._agent_id):
            if d.get("client_id") == ref.client_id or (
                ref.order_id and d.get("order_id") == ref.order_id
            ):
                return self._order_from_dict(d)
        return None

    @staticmethod
    def _order_from_dict(d: dict[str, Any]) -> Order:
        side = Side.SELL if str(d.get("side", "")).lower() == "sell" else Side.BUY
        return Order(
            ref=OrderRef(client_id=d.get("client_id", ""), order_id=d.get("order_id", "")),
            symbol=d.get("code", ""),
            side=side,
            qty=int(d.get("quantity", 0) or 0),
            filled=int(d.get("filled", 0) or 0),
            price=float(d.get("price", 0) or 0),
            state=_STATE_MAP.get(d.get("state", ""), OrderState.PENDING),
            text=d.get("text", "") or "",
            ts_ms=int(d.get("ts_unix_ms", 0) or 0),
        )

    # ---- [9] connection ----
    async def connection(self) -> ConnState:
        self._require(Capability.CONNECTION)
        store = self._need_store()
        status = store.status(self._agent_id)
        if not status:
            return ConnState(
                broker_up=False, exchange_up=False, link=LinkState.DOWN,
                last_seen_ms=_now_ms(), detail="no QUIK agent connected",
            )
        # Pick the freshest agent's lamp.
        best = min(status, key=lambda r: r.get("last_seen_age_ms", 1 << 62))
        lamp = best.get("link", "red")
        link = {
            "green": LinkState.UP,
            "yellow": LinkState.DEGRADED,
            "red": LinkState.DOWN,
        }.get(lamp, LinkState.DOWN)
        up = lamp in _WORKING_LINKS
        return ConnState(
            broker_up=up,
            exchange_up=up,
            link=link,
            last_seen_ms=int(best.get("last_seen_ms", 0) or 0),
            detail=f"agent={best.get('agent_id', '')} lamp={lamp}",
        )


@register("quik")
def _build_quik(settings: Any, **inject: Any) -> QuikBroker:
    """Registry factory: build a QuikBroker. The app passes the shared stores/server
    from app.state via ``inject`` (quik_store / quik_order_store / quik_server)."""
    return QuikBroker(settings, **inject)
