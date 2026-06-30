"""Order store + sender — STL side (sprint02 Phase 2).

HUMAN-INITIATED only. Tracks working orders + executions per agent from incoming
OrderUpdate / TransReply / ExecutionUpdate, and builds the STL->agent order
messages (PlaceOrder / CancelOrder / KillSwitch / StartExecution / StopExecution)
that the server enqueues onto the agent's Session stream.

No strategy, no auto-placement: this module only stores state and packages
operator-decided commands (Guard 3). Daily-cap bookkeeping is here so the API
can re-check the limit before sending.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# pb is importable because trader.quik.__init__ put pb/ on sys.path.
import trader.quik  # noqa: F401
from shectory.quik.v1 import quik_agent_pb2 as pb

_SIDE_TO_PB = {"buy": pb.SIDE_BUY, "sell": pb.SIDE_SELL}
_PB_TO_SIDE = {pb.SIDE_BUY: "buy", pb.SIDE_SELL: "sell", pb.SIDE_UNSPECIFIED: ""}

_STATE_NAME = {
    pb.ORDER_STATE_UNSPECIFIED: "unspecified",
    pb.ORDER_STATE_PENDING: "pending",
    pb.ORDER_STATE_ACTIVE: "active",
    pb.ORDER_STATE_PARTIAL: "partial",
    pb.ORDER_STATE_FILLED: "filled",
    pb.ORDER_STATE_CANCELLED: "cancelled",
    pb.ORDER_STATE_REJECTED: "rejected",
}
# A working (resting) order still consumes the working-contracts budget.
_WORKING_STATES = {"pending", "active", "partial"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def side_to_pb(side: str) -> int:
    """'buy'/'sell' (case-insensitive) -> pb.Side. Unknown raises ValueError."""
    s = (side or "").strip().lower()
    if s not in _SIDE_TO_PB:
        raise ValueError(f"side must be buy|sell, got {side!r}")
    return _SIDE_TO_PB[s]


def state_name(state: int) -> str:
    return _STATE_NAME.get(state, "unspecified")


@dataclass
class OrderRecord:
    """Latest known state of one order, keyed by client_id."""

    client_id: str
    code: str
    side: str
    price: float
    quantity: int
    state: str = "pending"
    order_id: str = ""
    filled: int = 0
    text: str = ""
    ts_unix_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "code": self.code,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "state": self.state,
            "order_id": self.order_id,
            "filled": self.filled,
            "remaining": max(0, self.quantity - self.filled),
            "text": self.text,
            "ts_unix_ms": self.ts_unix_ms,
        }


@dataclass
class ExecutionRecord:
    """Latest maker-execution progress for one client_id (1b)."""

    client_id: str
    code: str
    target: int = 0
    filled: int = 0
    avg_price: float = 0.0
    state: str = "working"
    text: str = ""
    ts_unix_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "code": self.code,
            "target": self.target,
            "filled": self.filled,
            "avg_price": self.avg_price,
            "state": self.state,
            "text": self.text,
            "ts_unix_ms": self.ts_unix_ms,
        }


@dataclass
class _AgentOrders:
    orders: dict[str, OrderRecord] = field(default_factory=dict)
    executions: dict[str, ExecutionRecord] = field(default_factory=dict)
    trans_replies: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False          # set by KillSwitch; cleared explicitly
    placed_count: dict[str, int] = field(default_factory=dict)  # YYYY-MM-DD -> n


class OrderStore:
    """Thread/async-safe per-agent order + execution state.

    Writes come from the gRPC server coroutine (incoming agent messages) and from
    the API when it enqueues a placement; reads come from FastAPI handlers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, _AgentOrders] = {}

    def _bucket(self, agent_id: str) -> _AgentOrders:
        b = self._agents.get(agent_id)
        if b is None:
            b = _AgentOrders()
            self._agents[agent_id] = b
        return b

    # ---- daily cap + kill-switch state ----
    def placed_today(self, agent_id: str) -> int:
        key = date.today().isoformat()
        with self._lock:
            return self._bucket(agent_id).placed_count.get(key, 0)

    def record_placement(self, agent_id: str) -> None:
        key = date.today().isoformat()
        with self._lock:
            b = self._bucket(agent_id)
            b.placed_count[key] = b.placed_count.get(key, 0) + 1

    def is_blocked(self, agent_id: str) -> bool:
        with self._lock:
            return self._bucket(agent_id).blocked

    def set_blocked(self, agent_id: str, blocked: bool) -> None:
        with self._lock:
            self._bucket(agent_id).blocked = blocked

    def working_contracts(self, agent_id: str) -> int:
        """Total contracts still resting (pending/active/partial), unfilled remainder."""
        with self._lock:
            b = self._bucket(agent_id)
            return sum(
                max(0, o.quantity - o.filled)
                for o in b.orders.values()
                if o.state in _WORKING_STATES
            )

    # ---- register a placement locally (PENDING) before the agent replies ----
    def register_pending(
        self, agent_id: str, client_id: str, code: str, side: str,
        price: float, quantity: int,
    ) -> None:
        with self._lock:
            b = self._bucket(agent_id)
            b.orders[client_id] = OrderRecord(
                client_id=client_id, code=code, side=side,
                price=price, quantity=quantity, state="pending",
            )

    # ---- register a native MOVE locally (optimistic) before the agent confirms ----
    def register_replace(
        self, agent_id: str, client_id: str, new_price: float,
        new_quantity: int = 0,
    ) -> None:
        """Reflect a native MOVE on the local record (optimistic). Price is updated;
        quantity only when new_quantity > 0 (0 = keep). order_id is left unchanged —
        QUIK assigns a new one on the move, which arrives via the next OrderUpdate. A
        move does NOT count against the daily cap (it re-prices, it does not place)."""
        with self._lock:
            b = self._bucket(agent_id)
            rec = b.orders.get(client_id)
            if rec is None:
                return
            rec.price = float(new_price)
            if int(new_quantity) > 0:
                rec.quantity = int(new_quantity)
            rec.ts_unix_ms = _now_ms()

    # ---- writes from incoming agent messages ----
    def apply_order_update(self, agent_id: str, ou: "pb.OrderUpdate") -> None:
        with self._lock:
            b = self._bucket(agent_id)
            rec = b.orders.get(ou.client_id)
            if rec is None:
                rec = OrderRecord(
                    client_id=ou.client_id, code=ou.code,
                    side=_PB_TO_SIDE.get(ou.side, ""),
                    price=ou.price, quantity=ou.quantity,
                )
                b.orders[ou.client_id] = rec
            rec.order_id = ou.order_id or rec.order_id
            rec.code = ou.code or rec.code
            if ou.side:
                rec.side = _PB_TO_SIDE.get(ou.side, rec.side)
            if ou.price:
                rec.price = ou.price
            if ou.quantity:
                rec.quantity = ou.quantity
            rec.filled = ou.filled
            rec.state = state_name(ou.state)
            rec.text = ou.text
            rec.ts_unix_ms = ou.ts_unix_ms or _now_ms()

    def apply_trans_reply(self, agent_id: str, tr: "pb.TransReply") -> None:
        with self._lock:
            b = self._bucket(agent_id)
            b.trans_replies.append({
                "client_id": tr.client_id,
                "trans_id": tr.trans_id,
                "result_code": tr.result_code,
                "text": tr.text,
                "ts_unix_ms": tr.ts_unix_ms or _now_ms(),
            })
            # keep the last 200 replies
            if len(b.trans_replies) > 200:
                b.trans_replies = b.trans_replies[-200:]

    def apply_execution_update(self, agent_id: str, eu: "pb.ExecutionUpdate") -> None:
        with self._lock:
            b = self._bucket(agent_id)
            rec = b.executions.get(eu.client_id)
            if rec is None:
                rec = ExecutionRecord(client_id=eu.client_id, code=eu.code)
                b.executions[eu.client_id] = rec
            rec.code = eu.code or rec.code
            rec.target = eu.target
            rec.filled = eu.filled
            rec.avg_price = eu.avg_price
            rec.state = eu.state or rec.state
            rec.text = eu.text
            rec.ts_unix_ms = eu.ts_unix_ms or _now_ms()

    # ---- reads (from FastAPI) ----
    def working_orders(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            buckets = self._select(agent_id)
            out: list[dict[str, Any]] = []
            for aid, b in buckets:
                for rec in b.orders.values():
                    d = rec.to_dict()
                    d["agent_id"] = aid
                    out.append(d)
            out.sort(key=lambda d: d["ts_unix_ms"], reverse=True)
            return out

    def executions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            buckets = self._select(agent_id)
            out: list[dict[str, Any]] = []
            for aid, b in buckets:
                for rec in b.executions.values():
                    d = rec.to_dict()
                    d["agent_id"] = aid
                    out.append(d)
            out.sort(key=lambda d: d["ts_unix_ms"], reverse=True)
            return out

    def trans_replies(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            buckets = self._select(agent_id)
            out: list[dict[str, Any]] = []
            for aid, b in buckets:
                for tr in b.trans_replies:
                    out.append({**tr, "agent_id": aid})
            out.sort(key=lambda d: d["ts_unix_ms"], reverse=True)
            return out

    def _select(self, agent_id: str | None) -> list[tuple[str, _AgentOrders]]:
        if agent_id is not None:
            b = self._agents.get(agent_id)
            return [(agent_id, b)] if b is not None else []
        return list(self._agents.items())


# ---- STL -> agent message builders (wrapped in OrchestratorMessage) ----

def build_place_order(
    client_id: str, code: str, side: str, price: float, quantity: int, collar: float,
) -> "pb.OrchestratorMessage":
    return pb.OrchestratorMessage(
        place_order=pb.PlaceOrder(
            client_id=client_id, code=code, side=side_to_pb(side),
            price=float(price), quantity=int(quantity), collar=float(collar),
        )
    )


def build_cancel_order(client_id: str, order_id: str = "") -> "pb.OrchestratorMessage":
    return pb.OrchestratorMessage(
        cancel_order=pb.CancelOrder(client_id=client_id, order_id=order_id or "")
    )


def build_replace_order(
    client_id: str, order_id: str, new_price: float, new_quantity: int = 0,
) -> "pb.OrchestratorMessage":
    """Native atomic move: re-price (and optionally re-size) a resting order in ONE
    QUIK MOVE_ORDERS transaction. new_quantity 0 = keep current quantity. The agent
    re-checks the collar on new_price and never widens qty past the per-order cap."""
    return pb.OrchestratorMessage(
        replace_order=pb.ReplaceOrder(
            client_id=client_id, order_id=order_id or "",
            new_price=float(new_price), new_quantity=int(new_quantity),
        )
    )


def build_kill_switch(reason: str) -> "pb.OrchestratorMessage":
    return pb.OrchestratorMessage(kill_switch=pb.KillSwitch(reason=reason or ""))


def build_set_limits(
    instrument_whitelist, max_contracts_per_order: int, max_working_contracts: int,
    price_collar_frac: float, daily_order_cap: int,
) -> "pb.OrchestratorMessage":
    """STL -> agent: push the hard limits + whitelist (STL is the source of truth). The
    agent adopts the whitelist and treats the caps as a ceiling it may only tighten; the
    master flag (trading_enabled) is NOT pushed (stays dual). See proto SetLimits."""
    return pb.OrchestratorMessage(
        set_limits=pb.SetLimits(
            instrument_whitelist=list(instrument_whitelist),
            max_contracts_per_order=int(max_contracts_per_order),
            max_working_contracts=int(max_working_contracts),
            price_collar_frac=float(price_collar_frac),
            daily_order_cap=int(daily_order_cap),
        )
    )


def build_start_execution(
    client_id: str, code: str, side: str, target_quantity: int,
    worst_price: float, allow_cross: bool = False,
) -> "pb.OrchestratorMessage":
    return pb.OrchestratorMessage(
        start_execution=pb.StartExecution(
            client_id=client_id, code=code, side=side_to_pb(side),
            target_quantity=int(target_quantity), worst_price=float(worst_price),
            allow_cross=bool(allow_cross),
        )
    )


def build_stop_execution(client_id: str) -> "pb.OrchestratorMessage":
    return pb.OrchestratorMessage(
        stop_execution=pb.StopExecution(client_id=client_id)
    )
