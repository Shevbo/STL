from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from trader.pos.models import AccountSummary, Position


@dataclass
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    status: str
    fill_price: float | None = None


class STLRuntime(Protocol):
    async def get_quote(self, symbol: str) -> Any: ...
    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]: ...
    async def get_orderbook(self, symbol: str) -> Any: ...
    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order: ...
    async def cancel_order(self, order_id: str) -> None: ...
    async def get_orders(self) -> list[Order]: ...
    async def get_position(self, symbol: str) -> Position: ...
    async def get_account(self) -> AccountSummary: ...
    def get_state(self, key: str, default: Any = None) -> Any: ...
    def set_state(self, key: str, value: Any) -> None: ...
    def log(self, msg: str, level: str = "info") -> None: ...


class BacktestRuntime:
    def __init__(self, bars: list[Bar], symbol: str, initial_equity: float) -> None:
        self._bars = bars
        self._symbol = symbol
        self._equity = initial_equity
        self._cursor = min(4, len(bars) - 1)
        self._positions: dict[str, dict] = {}
        self._orders: list[Order] = []
        self._state: dict[str, Any] = {}
        self._logs: list[str] = []

    def advance(self) -> bool:
        if self._cursor >= len(self._bars) - 2:
            return False
        self._cursor += 1
        return True

    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]:
        start = max(0, self._cursor - n + 1)
        return self._bars[start : self._cursor + 1]

    async def get_quote(self, symbol: str) -> Any:
        bar = self._bars[self._cursor]
        return {"bid": bar.close, "ask": bar.close, "last": bar.close}

    async def get_orderbook(self, symbol: str) -> Any:
        return {"bids": [], "asks": []}

    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order:
        order_id = uuid4().hex[:12]
        next_bar = self._bars[self._cursor + 1]
        fill_price = next_bar.open
        order = Order(
            order_id=order_id, symbol=symbol, side=side,
            qty=qty, price=price, status="filled", fill_price=fill_price,
        )
        self._orders.append(order)
        pos = self._positions.get(symbol, {"side": "flat", "qty": 0, "avg": 0.0})
        if side == "buy":
            new_qty = pos["qty"] + qty
            pos = {"side": "long", "qty": new_qty, "avg": fill_price}
        else:
            new_qty = pos["qty"] - qty
            if new_qty > 0:
                pos = {"side": "long", "qty": new_qty, "avg": pos["avg"]}
            elif new_qty < 0:
                pos = {"side": "short", "qty": abs(new_qty), "avg": fill_price}
            else:
                pos = {"side": "flat", "qty": 0, "avg": 0.0}
        self._positions[symbol] = pos
        return order

    async def cancel_order(self, order_id: str) -> None:
        pass

    async def get_orders(self) -> list[Order]:
        return list(self._orders)

    async def get_position(self, symbol: str) -> Position:
        pos = self._positions.get(symbol, {"side": "flat", "qty": 0, "avg": 0.0})
        return Position(
            symbol=symbol, account_id="backtest",
            side=pos["side"], quantity=pos["qty"],
            avg_price=Decimal(str(pos["avg"])),
            current_price=Decimal(str(self._bars[self._cursor].close)),
            var_margin=Decimal("0"),
        )

    async def get_account(self) -> AccountSummary:
        return AccountSummary(
            deposit=Decimal(str(self._equity)),
            free=Decimal(str(self._equity)),
            in_position=Decimal("0"),
            variation_margin=Decimal("0"),
        )

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

    def log(self, msg: str, level: str = "info") -> None:
        self._logs.append(f"[{level}] {msg}")


class LiveRuntime:
    """STL runtime for live trading — wraps existing trader clients."""

    def __init__(self, robot_id: str, pool, tx_client=None, pos_client=None) -> None:
        self._robot_id = robot_id
        self._pool = pool
        self._tx = tx_client
        self._pos = pos_client
        self._state: dict[str, Any] = {}
        self._state_loaded = False

    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]:
        raise NotImplementedError("Wire to WsHub bars cache in Task 11")

    async def get_quote(self, symbol: str) -> Any:
        raise NotImplementedError("Wire to feed in Task 11")

    async def get_orderbook(self, symbol: str) -> Any:
        raise NotImplementedError("Wire to book_stream in Task 11")

    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order:
        from trader.tx.models import OrderRequest
        req = OrderRequest(symbol=symbol, side=side, quantity=qty, price=price)
        resp = await self._tx.place_order(req)
        return Order(order_id=resp.order_id, symbol=symbol, side=side,
                     qty=qty, price=price, status=resp.status)

    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError("Wire to TxClient in Task 11")

    async def get_orders(self) -> list[Order]:
        raise NotImplementedError("Wire to TxClient in Task 11")

    async def get_position(self, symbol: str) -> Position:
        portfolio = await self._pos.get_portfolio()
        for p in portfolio:
            if p.symbol == symbol:
                return p
        return Position(symbol=symbol, account_id="", side="flat",
                        quantity=0, current_price=Decimal(0), var_margin=Decimal(0))

    async def get_account(self) -> AccountSummary:
        return await self._pos.get_account_summary()

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

    def log(self, msg: str, level: str = "info") -> None:
        import structlog
        structlog.get_logger().msg(msg, robot_id=self._robot_id, level=level)

    async def flush_state(self) -> None:
        if self._pool is None:
            return
        import json
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE robots SET state_json = $1 WHERE id = $2",
                json.dumps(self._state), self._robot_id,
            )
