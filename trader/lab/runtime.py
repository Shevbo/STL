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
    fill_time: int | None = None    # unix timestamp of fill bar


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
    def __init__(self, bars: list[Bar], symbol: str, initial_equity: float,
                 point_value: float = 1.0) -> None:
        self._bars = bars
        self._symbol = symbol
        self._equity = initial_equity
        # RUB value of one index point (= step_price / min_step). Without it,
        # PnL is in raw index points, not rubles. RIM6 ≈ 1.42 ₽/point.
        self._point_value = point_value or 1.0
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
            qty=qty, price=price, status="filled",
            fill_price=fill_price, fill_time=next_bar.time,
        )
        self._orders.append(order)
        pos = self._positions.get(symbol, {"side": "flat", "qty": 0, "avg": 0.0})

        # Work in SIGNED position space so buy/sell handle long, short and
        # flips symmetrically. (Old code always grew a long on buy → a buy that
        # should cover a short instead doubled the position.)
        signed = pos["qty"] if pos["side"] == "long" else (-pos["qty"] if pos["side"] == "short" else 0)
        avg = pos["avg"]
        delta = qty if side == "buy" else -qty
        new_signed = signed + delta

        # Realized PnL when the trade reduces the existing position (closing part/all).
        # Multiply by point_value → result in RUBLES, not raw index points.
        if signed != 0 and (signed > 0) != (delta > 0):
            closed = min(qty, abs(signed))
            if signed > 0:                       # closing a long
                self._equity += (fill_price - avg) * closed * self._point_value
            else:                                # closing a short
                self._equity += (avg - fill_price) * closed * self._point_value

        if new_signed == 0:
            pos = {"side": "flat", "qty": 0, "avg": 0.0}
        elif (signed >= 0) == (new_signed > 0) and signed != 0 and (signed > 0) == (delta > 0):
            # Same-direction increase → weighted-average entry price.
            total = abs(signed) + qty
            avg = (avg * abs(signed) + fill_price * qty) / total
            pos = {"side": "long" if new_signed > 0 else "short", "qty": abs(new_signed), "avg": avg}
        else:
            # Opened fresh, or flipped through zero → entry at fill price.
            pos = {"side": "long" if new_signed > 0 else "short", "qty": abs(new_signed), "avg": fill_price}

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
    """
    STL runtime for live trading — wraps existing trader clients.

    Market data (get_bars/get_quote) comes from MOEX ISS (free, fresh) — same
    source as the backtest, so live behaviour matches backtest behaviour. We do
    not depend on WsHub being subscribed to the robot's symbol.

    SAFETY: `paper` mode (default True) does NOT send orders to Finam. It records
    intended fills into live_trades with status='paper' so the robot can be proven
    against live data at zero financial risk. Real trading requires paper=False,
    set only after explicit user confirmation.
    """

    def __init__(self, robot_id: str, pool, tx_client=None, pos_client=None,
                 paper: bool = True) -> None:
        self._robot_id = robot_id
        self._pool = pool
        self._tx = tx_client
        self._pos = pos_client
        self._paper = paper
        self._state: dict[str, Any] = {}
        self._bars_cache: list[Bar] | None = None
        self._bars_symbol: str | None = None

    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]:
        # Fetch a recent minute window from ISS, slice the last n. Cached per tick
        # so multiple get_bars calls in one on_bar don't re-hit ISS.
        if self._bars_cache is not None and self._bars_symbol == symbol:
            return self._bars_cache[-n:] if n else self._bars_cache
        from datetime import date, timedelta
        from trader.lab.iss_loader import load_bars_iss
        # ~900 trading minutes/day; cover n minutes + buffer, min 3 calendar days
        days = max(3, (n // 800) + 3)
        today = date.today()
        try:
            bars = await load_bars_iss(symbol, today - timedelta(days=days), today, interval=1)
        except Exception:
            bars = []
        self._bars_cache = bars
        self._bars_symbol = symbol
        return bars[-n:] if n else bars

    async def get_quote(self, symbol: str) -> Any:
        bars = await self.get_bars(symbol, tf=1, n=1)
        if bars:
            c = bars[-1].close
            return {"bid": c, "ask": c, "last": c}
        return {"bid": 0.0, "ask": 0.0, "last": 0.0}

    async def get_orderbook(self, symbol: str) -> Any:
        return {"bids": [], "asks": []}

    @staticmethod
    def _finam_symbol(symbol: str) -> str:
        """
        ISS uses bare ticker (RIM6); Finam Trade API expects symbol@MIC
        (RIM6@RTSX) — same form the account positions use. Append @RTSX for
        bare FORTS tickers so orders net against existing positions correctly.
        """
        return symbol if "@" in symbol else f"{symbol}@RTSX"

    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order:
        from uuid import uuid4
        if self._paper:
            # Virtual fill — record, do NOT touch the broker.
            oid = "paper-" + uuid4().hex[:10]
            await self._record_trade(symbol, side, qty, price, oid, "paper")
            self.log(f"[PAPER] {side} {qty} {symbol} @ {price:.0f}")
            return Order(order_id=oid, symbol=symbol, side=side, qty=qty,
                         price=price, status="paper", fill_price=price)
        # REAL order to Finam — use the @MIC-qualified symbol the broker expects.
        fin_sym = self._finam_symbol(symbol)
        from trader.tx.models import OrderRequest
        req = OrderRequest(symbol=fin_sym, side=side, quantity=qty, price=price)
        try:
            resp = await self._tx.place_order(req)
        except Exception as exc:
            msg = str(exc)
            # Translate Finam's server-side risk rejection into a clear log.
            if "[666]" in msg or "uncovered" in msg.lower() or "непокрыт" in msg.lower():
                self.log(f"[LIVE] BROKER REJECT (uncovered-position risk) {side} {qty} {fin_sym}: "
                         f"check Finam account risk level / margin permission", level="error")
            else:
                self.log(f"[LIVE] order failed {side} {qty} {fin_sym}: {msg}", level="error")
            await self._record_trade(symbol, side, qty, price, "rejected", "rejected")
            raise
        await self._record_trade(symbol, side, qty, price, resp.order_id, resp.status)
        self.log(f"[LIVE] {side} {qty} {fin_sym} @ {price:.0f} -> {resp.status}")
        return Order(order_id=resp.order_id, symbol=symbol, side=side,
                     qty=qty, price=price, status=resp.status)

    async def _record_trade(self, symbol, side, qty, price, order_id, status) -> None:
        if self._pool is None:
            return
        from uuid import uuid4
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO live_trades
                       (id, robot_id, symbol, side, qty, price, order_id, status, timestamp)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())""",
                    uuid4().hex, self._robot_id, symbol, side, qty,
                    Decimal(str(price)), order_id, status,
                )
        except Exception as exc:
            import structlog
            structlog.get_logger().warning("live.record_trade_failed", error=str(exc))

    async def cancel_order(self, order_id: str) -> None:
        return  # market-style robots don't hold resting orders

    async def get_orders(self) -> list[Order]:
        return []  # no resting-order tracking for these robots

    async def get_position(self, symbol: str) -> Position:
        # In paper mode the broker position is irrelevant — reconstruct from
        # our recorded paper fills so the robot sees its own virtual position.
        if self._paper:
            return await self._paper_position(symbol)
        portfolio = await self._pos.get_portfolio()
        for p in portfolio:
            if p.symbol == symbol:
                return p
        return Position(symbol=symbol, account_id="", side="flat",
                        quantity=0, avg_price=Decimal(0), current_price=Decimal(0), var_margin=Decimal(0))

    async def _paper_position(self, symbol: str) -> Position:
        signed = 0
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT side, qty FROM live_trades
                       WHERE robot_id=$1 AND symbol=$2 AND status='paper'
                       ORDER BY timestamp""",
                    self._robot_id, symbol,
                )
            for r in rows:
                signed += r["qty"] if r["side"] == "buy" else -r["qty"]
        side = "long" if signed > 0 else ("short" if signed < 0 else "flat")
        return Position(symbol=symbol, account_id="paper", side=side,
                        quantity=abs(signed), avg_price=Decimal(0),
                        current_price=Decimal(0), var_margin=Decimal(0))

    async def get_account(self) -> AccountSummary:
        if self._paper:
            return AccountSummary(deposit=Decimal("100000"), free=Decimal("100000"),
                                  in_position=Decimal("0"), variation_margin=Decimal("0"))
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
