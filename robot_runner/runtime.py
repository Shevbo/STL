"""AgentRuntime — the STLRuntime protocol backed by the local agent bridge.

Strategies from trader/lab/strategies/library.py run against this runtime
UNCHANGED (same protocol LiveRuntime implements in STL). Differences:
 - bars come from local QUIK DDE ticks (BarBuilder), not ISS;
 - orders go to the agent's trade.Manager -> QUIK Lua bridge;
 - position/P&L are tracked locally from OrderUpdate fills (source of truth
   on this box; STL only mirrors it via RobotStatusReport).

Limits: max_position is enforced BEFORE sending (first line; the Go Guard is
the second). An order that would exceed it returns status='skipped'.
"""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog

from trader.lab.runtime import Bar, Order
from trader.pos.models import AccountSummary, Position

log = structlog.get_logger()

_FILLED_STATE = 4    # quik_agent.proto OrderState.ORDER_STATE_FILLED
_PARTIAL_STATE = 3
_QUOTE_FRESH_MS = 10_000   # a quote younger than this prices at the exchange touch
# When the quote is STALE/absent we cross the best reference by this fraction so a REAL
# order still crosses the live market instead of resting (an EXIT must fill). RIU6 ~80k
# → 0.3% ≈ 240 pts ≈ 24 steps: well past the ~1-step spread, inside the exchange/agent
# price collar. The stuck-exit bug (2026-07-20) fell back to bars[-1].close = a resting
# limit above the market that never filled while the robot sat long +11.
_STALE_CROSS_FRAC = 0.003
# Переворот сигнала уходит ДВУМЯ заявками подряд (library.py: сначала выход всей
# позицией, следом вход в новую сторону). В бэктесте обе исполняются в одном баре,
# вживую выход ещё висит на рынке, когда приходит вход, и QUIK бьёт по нему
# «Обработка кросс-заявок блокирована»: вход теряется, робот остаётся в флэте
# вместо разворота. Даём встречной заявке сойти с рынка. По истечении ждать
# нечего — отправляем как раньше: пропустить вход значит потерять переворот целиком.
_CROSS_WAIT_SEC = 5.0
_STATE_TO_STATUS = {2: "active", 3: "partial", 4: "filled",
                    5: "cancelled", 6: "rejected"}

# Per-robot detailed log ("Детальный лог робота" on the stand): significant events
# only (orders/fills/signals/errors/lifecycle), appended across runner restarts.
_MSK = timezone(timedelta(hours=3))          # FORTS wall clock; RU has no DST
_LOG_MAX_BYTES = 1_048_576                   # trim when the file passes 1 MiB…
_LOG_KEEP_BYTES = 262_144                    # …down to the last 256 KiB


def _ts_msk() -> str:
    return datetime.now(_MSK).strftime("%Y-%m-%d %H:%M:%S")


def _trim_log(path: str, keep: int) -> None:
    """Keep only the last `keep` bytes of path, dropping the partial first line.
    ponytail: size cap in lieu of a rotation lib — the stand tails 64 KiB anyway."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - keep))
            tail = f.read()
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]
        with open(path, "wb") as f:
            f.write(tail)
    except OSError:
        pass


class AgentRuntime:
    def __init__(self, robot_id: str, bridge, bars, *, max_position: int = 1,
                 paper: bool = False, state: dict | None = None,
                 fills_log=None, quote_fn=None, event_log_dir: str | None = None) -> None:
        self._robot_id = robot_id
        self._bridge = bridge
        self._bars = bars
        self._max_position = max(1, int(max_position))
        self._paper = paper
        self._state: dict[str, Any] = dict(state or {})
        self._fills_log = fills_log          # optional callable(dict) for persistence
        # quote_fn() -> (bid, ask, ts_ms) | None: freshest QUIK quote for THIS
        # robot's symbol (fed by the host's tick consumer). Real orders price
        # marketable off it; paper never uses it (same-bar close fills).
        self._quote_fn = quote_fn
        # «Только на выход»: робот доводит открытую позицию до закрытия по своему
        # же сигналу (TP/SL/разворот) и НЕ открывает новую. Нужен, чтобы вывести
        # робота из боя без обрыва сделки: на экспирации контракта, при разводе
        # встречных роботов (кросс-заявки), перед переводом в бумагу. Значение
        # приходит из params робота (host выставляет его перед каждым баром).
        self.exit_only = False
        self._signed = 0
        self._avg = 0.0
        self._realized = 0.0          # GROSS price points (entry↔exit diff), no fees
        self._commission = 0.0        # cumulative TAKER commission in points (see _apply_fill)
        self._fills: list[dict] = []
        self._seq = 0
        self._orders: dict[str, Order] = {}  # client_id -> last known state
        # Per-robot detailed log file (<data_dir>/logs/<robot_id>.log); None disables.
        self._event_log_path: str | None = None
        if event_log_dir:
            try:
                d = os.path.join(event_log_dir, "logs")
                os.makedirs(d, exist_ok=True)
                self._event_log_path = os.path.join(d, f"{robot_id}.log")
            except OSError:
                self._event_log_path = None

    # ---- STLRuntime protocol ----

    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]:
        return self._bars.bars(n)

    async def get_quote(self, symbol: str) -> Any:
        bars = self._bars.bars(1)
        c = bars[-1].close if bars else 0.0
        return {"bid": c, "ask": c, "last": c}

    async def get_orderbook(self, symbol: str) -> Any:
        return {"bids": [], "asks": []}

    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order:
        delta = qty if side == "buy" else -qty
        # Режим «только на выход»: наружу пропускаем ТОЛЬКО то, что уменьшает
        # позицию. Заявку на разворот (через ноль) урезаем до закрытия — робот
        # выходит, но в противоположную сторону не встаёт.
        if self.exit_only:
            if self._signed == 0 or (delta > 0) == (self._signed > 0):
                self.event("SKIP", f"{side} {qty} {symbol}: режим «только на выход», "
                           f"новых позиций не открываем (позиция {self._signed})")
                return Order(order_id="skipped-exitonly", symbol=symbol, side=side,
                             qty=qty, price=price, status="skipped")
            if qty > abs(self._signed):
                self.event("TRIM", f"{side} {qty} -> {abs(self._signed)} {symbol}: "
                           "режим «только на выход», разворот урезан до закрытия")
                qty = abs(self._signed)
                delta = qty if side == "buy" else -qty
        # Reducing is always allowed; growing beyond max_position is refused.
        grows = abs(self._signed + delta) > abs(self._signed)
        if grows and abs(self._signed + delta) > self._max_position:
            self.event("SKIP", f"{side} {qty} {symbol}: превышен потолок "
                       f"max_position={self._max_position} (позиция {self._signed})",
                       level="warning")
            return Order(order_id="skipped-maxpos", symbol=symbol, side=side,
                         qty=qty, price=price, status="skipped")
        self._seq += 1
        client_id = f"rr:{self._robot_id}:{self._seq}:{uuid4().hex[:6]}"
        if self._paper:
            self._apply_fill(side, qty, price, client_id=client_id, symbol=symbol,
                             status="paper")
            return Order(order_id=client_id, symbol=symbol, side=side, qty=qty,
                         price=price, status="paper", fill_price=price)
        await self._await_opposite_clear(side, symbol)
        # REAL orders go MARKETABLE: BUY at the ask, SELL at the bid (freshest
        # QUIK quote), so the fill matches the backtest's fill-at-close model.
        # A limit at bars[-1].close RESTS whenever the market moved during the
        # bar -> the position never flips -> the strategy re-emits every bar
        # (stacking, seen live). bid/ask are exchange prices (on-step) and are
        # inside the agent's price collar by construction. Stale/absent quote
        # falls back to the strategy price (old behaviour).
        send_price = price
        ref, fresh = 0.0, False
        if self._quote_fn is not None:
            try:
                q = self._quote_fn()
            except Exception:  # noqa: BLE001 — quote is best-effort
                q = None
            if q:
                bid, ask, ts_ms = q
                ref = float((ask if side == "buy" else bid) or 0)
                fresh = ref > 0 and (time.time() * 1000 - float(ts_ms or 0)) <= _QUOTE_FRESH_MS
        if fresh:
            send_price = ref                       # fresh exchange touch → marketable
        else:
            # No fresh quote: CROSS the best reference so the order still crosses the
            # live market (an EXIT must not rest). Prefer the (possibly-stale) exchange
            # touch over the strategy bar-close, which drifts furthest on a fast tape.
            base = ref if ref > 0 else price
            collar = base * _STALE_CROSS_FRAC
            send_price = base - collar if side == "sell" else base + collar
        try:
            await self._bridge.place_order(client_id=client_id, code=symbol,
                                           side=side, price=send_price, qty=qty)
        except Exception as exc:
            self.event("REJECT", f"{side} {qty} {symbol} @ {send_price:.0f}: {exc}",
                       level="error")
            self._record(client_id, symbol, side, qty, send_price, "rejected")
            return Order(order_id=client_id, symbol=symbol, side=side, qty=qty,
                         price=send_price, status="rejected")
        mk = " (маркетируемая)" if send_price != price else ""
        self.event("ORDER", f"{side} {qty} {symbol} @ {send_price:.0f}{mk}")
        order = Order(order_id=client_id, symbol=symbol, side=side, qty=qty,
                      price=send_price, status="submitted")
        self._orders[client_id] = order
        return order

    async def _await_opposite_clear(self, side: str, symbol: str) -> None:
        """Ждём, пока встречная заявка по этому инструменту сойдёт с рынка (см.
        _CROSS_WAIT_SEC). Цену считаем ПОСЛЕ ожидания — за эти секунды рынок уходит."""
        def blocking() -> int:
            return sum(o.qty for o in self._orders.values()
                       if o.symbol == symbol and o.side != side
                       and o.status in ("submitted", "active", "partial"))
        if not blocking():
            return
        deadline = time.time() + _CROSS_WAIT_SEC
        while time.time() < deadline:
            await asyncio.sleep(0.2)
            if not blocking():
                return
        self.event("WAIT", f"{side} {symbol}: встречная заявка на {blocking()} не сошла "
                   f"за {_CROSS_WAIT_SEC:.0f} с, отправляю как есть", level="warning")

    async def cancel_order(self, order_id: str) -> None:
        self.event("CANCEL", order_id, console=False)
        await self._bridge.cancel_order(order_id, "")

    def expire_order(self, client_id: str) -> None:
        """Locally terminate an order the agent cannot cancel (unknown to it —
        e.g. left from before an agent restart, or day-expired at QUIK). Keeping
        it 'submitted' forever made the runner's book PHANTOM (seen live: 8
        non-existent BUYs displayed for a day). If it somehow fills later,
        on_order_event still applies the fill by client_id prefix."""
        o = self._orders.get(client_id)
        if o is not None and o.status in ("submitted", "active", "partial"):
            self._orders[client_id] = Order(order_id=client_id, symbol=o.symbol,
                                            side=o.side, qty=o.qty,
                                            price=o.price, status="expired")

    async def get_orders(self) -> list[Order]:
        return [o for o in self._orders.values()
                if o.status in ("submitted", "active", "partial")]

    async def get_position(self, symbol: str) -> Position:
        side = "long" if self._signed > 0 else ("short" if self._signed < 0 else "flat")
        return Position(symbol=symbol, account_id="agent", side=side,
                        quantity=abs(self._signed), avg_price=Decimal(str(self._avg)),
                        current_price=Decimal("0"), var_margin=Decimal("0"))

    async def get_account(self) -> AccountSummary:
        return AccountSummary(deposit=Decimal("0"), free=Decimal("0"),
                              in_position=Decimal("0"), variation_margin=Decimal("0"))

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def state_snapshot(self) -> dict[str, Any]:
        """Read-only copy of the strategy's own state — the showcase explainer
        reads a standalone module's live exit levels (sl/tp/entry) from here."""
        return dict(self._state)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

    def log(self, msg: str, level: str = "info") -> None:
        log.msg(msg, robot_id=self._robot_id, level=level)

    def event(self, kind: str, msg: str, *, level: str = "info", console: bool = True) -> None:
        """Record a SIGNIFICANT event to the per-robot detailed log (the stand's
        «Детальный лог робота»), and by default mirror it to the console. Kinds:
        ORDER/CANCEL/FILL/SKIP/REJECT/FIX/SIGNAL/ERROR/LIFECYCLE. Best-effort:
        a disk error must never break trading."""
        if console:
            try:
                self.log(f"[{kind}] {msg}", level=level)
            except Exception:  # noqa: BLE001 — a console encoding error must never
                pass           # break trading (cp1251 pipe killed fills 2026-07-13)
        path = self._event_log_path
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{_ts_msk()} [{kind}] {msg}\n")
            if os.path.getsize(path) > _LOG_MAX_BYTES:
                _trim_log(path, _LOG_KEEP_BYTES)
        except Exception:  # noqa: BLE001 — logging must never break trading
            pass

    # ---- fills / position bookkeeping ----

    def on_order_event(self, u) -> None:
        """Feed an OrderUpdate from the agent. Fill states mutate the position."""
        cid = getattr(u, "client_id", "")
        if not cid.startswith(f"rr:{self._robot_id}:"):
            return
        state = getattr(u, "state", 0)
        order = self._orders.get(cid)
        if state in (_FILLED_STATE, _PARTIAL_STATE):
            qty = int(getattr(u, "filled", 0)) or int(getattr(u, "quantity", 0))
            if order is not None:
                side = order.side
            else:
                side = "buy" if getattr(u, "side", 1) == 1 else "sell"
            price = float(getattr(u, "price", 0.0))
            already = sum(f["qty"] for f in self._fills if f.get("client_id") == cid
                          and f.get("status") == "filled")
            fresh = max(0, qty - already)
            if fresh:
                self._apply_fill(side, fresh, price, client_id=cid,
                                 order_id=getattr(u, "order_id", ""),
                                 symbol=getattr(u, "code", ""),
                                 # journal at the EVENT's time, not receipt time:
                                 # the agent's journal-sync restores lost fills
                                 # hours later and they must land on their true
                                 # spot on the chart/session timeline.
                                 ts_ms=int(getattr(u, "ts_unix_ms", 0)) or None)
        if order is not None:
            status = _STATE_TO_STATUS.get(state, order.status)
            if status == "rejected" and order.status != "rejected":
                # Surface WHY the agent rejected (per-order/working cap, collar,
                # trading disabled). Previously silent: a real robot re-emitted a
                # capped close every bar with NO visible reason for ~13h and stayed
                # stuck long +14 (2026-07-21). ASCII-only wrapper; the agent's text
                # is English (non-ASCII in a hot log line is a loaded gun, 2026-07-13).
                self.event("REJECT", f"{order.side} {order.qty} {order.symbol} @ "
                           f"{order.price:.0f}: agent rejected -> "
                           f"{getattr(u, 'text', '') or 'no reason'}", level="error")
            self._orders[cid] = Order(order_id=cid, symbol=order.symbol,
                                      side=order.side, qty=order.qty,
                                      price=order.price, status=status)

    def _apply_fill(self, side: str, qty: int, price: float, *,
                    client_id: str = "", order_id: str = "", symbol: str = "",
                    status: str = "filled", ts_ms: int | None = None) -> None:
        # Same signed-space algorithm as BacktestRuntime.place_order.
        delta = qty if side == "buy" else -qty
        signed, avg = self._signed, self._avg
        if signed != 0 and (signed > 0) != (delta > 0):
            closed = min(qty, abs(signed))
            self._realized += ((price - avg) if signed > 0 else (avg - price)) * closed
        new_signed = signed + delta
        if new_signed == 0:
            self._signed, self._avg = 0, 0.0
        elif signed != 0 and (signed > 0) == (delta > 0):
            total = abs(signed) + qty
            self._avg = (avg * abs(signed) + price * qty) / total
            self._signed = new_signed
        elif signed != 0 and (new_signed > 0) == (signed > 0):
            # Partial reduce: fewer contracts at the SAME entry average. The old
            # else-branch reset avg to the closing fill's price, silently
            # re-basing the remaining contracts and mis-realizing every later
            # close (found 2026-07-13 reconstructing lost fills: -1583 pts of
            # truth became -5111 through the reset avg).
            self._signed = new_signed
        else:
            self._signed, self._avg = new_signed, price
        # Commission charged on EVERY fill (open + close), same TAKER model as the
        # backtest — the agent's real orders go MARKETABLE (cross the spread), so paper
        # must model taker too or its P&L flatters the strategy. Tracked in POINTS so it
        # subtracts directly from realized (also points); exact exchange fee, no ₽/point
        # needed (see commission.taker_points). NEVER let a fee calc break the trade path.
        fee_pts = 0.0
        try:
            from trader.lab.commission import taker_points
            fee_pts = taker_points(symbol or "", price, qty)
            self._commission += fee_pts
        except Exception:
            pass
        self._record(client_id or "fill", symbol, side, qty, price, status,
                     order_id=order_id, ts_ms=ts_ms)
        tag = "" if status == "filled" else f" ({status})"
        self.event("FILL", f"{side} {qty} @ {price:.0f}{tag} → позиция {self._signed} @ "
                   f"{self._avg:.0f}, реализовано {self._realized:.0f} п., "
                   f"комиссия {fee_pts:.1f} п. (Sum {self._commission:.0f}), "
                   f"чистыми {self._realized - self._commission:.0f} п.")

    def _record(self, client_id, symbol, side, qty, price, status, order_id="",
                ts_ms: int | None = None) -> None:
        f = {"client_id": client_id, "order_id": order_id or client_id,
             "symbol": symbol, "side": side, "qty": qty, "price": price,
             "status": status, "ts_ms": int(ts_ms or time.time() * 1000)}
        self._fills.append(f)
        if len(self._fills) > 200:
            self._fills = self._fills[-200:]
        if self._fills_log:
            try:
                self._fills_log(f)
            except Exception:  # noqa: BLE001 — persistence must never break trading
                pass

    def working_orders(self) -> list[dict]:
        """Resting/in-flight orders for the showcase (submitted/active/partial)."""
        return [{"client_id": o.order_id, "order_id": o.order_id, "side": o.side,
                 "price": o.price, "qty": o.qty, "state": o.status}
                for o in self._orders.values()
                if o.status in ("submitted", "active", "partial")]

    def signed_position(self) -> int:
        return self._signed

    def avg_price(self) -> float:
        return self._avg

    def realized_pnl(self) -> float:
        """Realized P&L reported to STL — NET of taker commission (points)."""
        return self._realized - self._commission

    def realized_gross(self) -> float:
        """Gross realized (points, pre-commission) — for persistence, so a restart
        restores gross + commission separately and never double-charges fees."""
        return self._realized

    def commission_points(self) -> float:
        return self._commission

    def apply_fix(self, *, position: int, avg: float, clear_working: bool,
                  note: str, symbol: str = "",
                  realized: float | None = None,
                  commission: float | None = None) -> None:
        """Recon align (fix_state): overwrite the believed book to the QUIK fact.

        Realized P&L is untouched by a plain fix — belief-correction is not a
        trade. But belief CAN be corrupted (2026-08-06: journal auto-heal
        replayed yesterday's evening fills, inflating realized by ~10k pts), so
        an EXPLICIT correction is allowed: realized/commission (both in POINTS,
        gross + fee kept apart, exactly as the runner tracks them) overwrite
        the counters when given. clear_working drops in-flight/resting order
        beliefs. Everything lands in the fill journal as a "fix_state" entry.
        """
        self._signed = int(position)
        self._avg = float(avg)
        if realized is not None:
            self._realized = float(realized)
        if commission is not None:
            self._commission = float(commission)
        if clear_working:
            self._orders = {cid: o for cid, o in self._orders.items()
                            if o.status not in ("submitted", "active", "partial")}
        status = f"fix_state: {note}" if note else "fix_state"
        self._record("recon", symbol, "fix", int(position), float(avg), status)
        pnl = ""
        if realized is not None or commission is not None:
            pnl = (f" реализовано←{self._realized:.1f} п. "
                   f"комиссия←{self._commission:.1f} п. "
                   f"(чистыми {self._realized - self._commission:.1f} п.)")
        self.event("FIX", f"позиция←{position} avg←{avg:.0f} "
                   f"clear_working={clear_working}{pnl} note={note!r}", level="warning")

    def restore(self, *, position: int, avg: float, realized: float,
                commission: float = 0.0, fills: list | None = None) -> None:
        """Re-seed position bookkeeping from persisted runner state (zero-touch).
        `realized` is the GROSS points; `commission` the cumulative fee points."""
        self._signed = int(position)
        self._avg = float(avg)
        self._realized = float(realized)
        self._commission = float(commission)
        if fills:
            self._fills = list(fills)[-200:]

    def fills_tail(self) -> list[dict]:
        """Persistable order/fill history (last 200)."""
        return self._fills[-200:]

    @property
    def state(self) -> dict:
        return self._state
