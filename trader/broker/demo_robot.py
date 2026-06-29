"""Demo robot — trades through the BrokerInterface contract ONLY.

It imports nothing concrete: just ``trader.broker.base`` + ``trader.broker.registry``.
The concrete broker (finam / quik) is chosen from ``settings.exchange_interface``;
switching brokers is a config change, not a code change.

It demonstrates the safe robot pattern:
  1. get the broker from the registry (read-only gate while we are designing),
  2. check capabilities and degrade gracefully,
  3. read instrument + order book + account + connection,
  4. reconcile the exchange position against the robot's expectation,
  5. in DRY-RUN (default) PRINT where it would place / replace — never sends.

DRY-RUN is the default and market is closed: no live order leaves this file. Set
``dry_run=False`` only on a live, trade-ready broker during market hours.
"""

from __future__ import annotations

import structlog

from trader.broker.base import (
    BrokerInterface,
    Capability,
    OrderRequest,
    OrderType,
    Side,
)
from trader.broker.registry import get_broker

log = structlog.get_logger()


async def run_demo(
    settings,
    symbol: str,
    *,
    target_qty: int = 1,
    dry_run: bool = True,
    **inject,
) -> dict:
    """Run one observe-reconcile-(would-place) cycle. Returns a report dict.

    ``inject`` forwards adapter deps (QUIK stores/server) to the factory. With
    ``dry_run`` (default) the broker need only be readable, so we bypass the
    trade-ready gate; a real trading run would call ``get_broker(settings)`` with
    the gate ON and ``dry_run=False``.
    """
    broker: BrokerInterface = get_broker(
        settings, require_trade_ready=not dry_run, **inject
    )
    report: dict = {
        "broker": broker.name,
        "trade_ready": broker.is_trade_ready(),
        "missing_core": sorted(c.value for c in broker.missing_core()),
        "actions": [],
    }

    await broker.connect()
    try:
        # ---- 1. connection (never trade on a dead link) ----
        if broker.supports(Capability.CONNECTION):
            conn = await broker.connection()
            report["connection"] = {
                "broker_up": conn.broker_up,
                "exchange_up": conn.exchange_up,
                "link": conn.link.value,
            }

        # ---- 2. instrument (size/price math) ----
        instrument = None
        if broker.supports(Capability.INSTRUMENTS):
            instrument = await broker.instrument(symbol)
            report["instrument"] = {
                "symbol": instrument.symbol,
                "price_step": instrument.price_step,
                "step_cost": instrument.step_cost,
            }

        # ---- 3. order book (decide / route) ----
        best_bid = best_ask = None
        if broker.supports(Capability.ORDER_BOOK):
            book = await broker.order_book(symbol)
            best_bid = book.best_bid.price if book.best_bid else None
            best_ask = book.best_ask.price if book.best_ask else None
            report["book"] = {"best_bid": best_bid, "best_ask": best_ask}

        # ---- 4. account (over-leverage guard) ----
        if broker.supports(Capability.ACCOUNT):
            acct = await broker.account()
            report["account"] = {"free": acct.free, "margin_used": acct.margin_used}
        else:
            report["account"] = None  # degrade gracefully — adapter cannot report it

        # ---- 5. reconcile exchange position vs robot expectation ----
        if broker.supports(Capability.POSITIONS):
            diff = await broker.reconcile(symbol, robot_qty=0)
            report["reconcile"] = {
                "exchange_qty": diff.exchange_qty,
                "robot_qty": diff.robot_qty,
                "delta": diff.delta,
                "matched": diff.matched,
            }
        else:
            report["reconcile"] = None

        # ---- 6. where it WOULD place / replace (DRY-RUN: prints, never sends) ----
        # Quote just inside the touch; pure illustration, no signal logic here.
        want_price = best_bid if best_bid is not None else 0.0
        req = OrderRequest(
            symbol=symbol,
            side=Side.BUY,
            qty=target_qty,
            price=want_price,
            order_type=OrderType.LIMIT,
            client_id="demo-robot-0001",
        )
        if dry_run:
            report["actions"].append(
                f"DRY-RUN place: BUY {target_qty} {symbol} @ {want_price} "
                f"(would call broker.place_order)"
            )
            if broker.supports(Capability.REPLACE_ORDER) and best_ask is not None:
                report["actions"].append(
                    f"DRY-RUN replace: move resting order -> {best_ask} "
                    f"(would call broker.replace_order, native atomic MOVE)"
                )
            else:
                report["actions"].append(
                    "replace_order NOT supported by this adapter -> robot must "
                    "cancel+re-place or wait (no silent emulation)"
                )
        else:  # pragma: no cover - guarded; only on a live trade-ready broker
            ref = await broker.place_order(req)
            report["actions"].append(f"placed order ref={ref.client_id}")
        return report
    finally:
        await broker.disconnect()


def print_report(report: dict) -> None:
    """Human-readable dump (used by the __main__ smoke run)."""
    print(f"broker={report['broker']} trade_ready={report['trade_ready']}")
    if report["missing_core"]:
        print(f"  missing CORE: {report['missing_core']}")
    for key in ("connection", "instrument", "book", "account", "reconcile"):
        if key in report:
            print(f"  {key}: {report[key]}")
    for action in report["actions"]:
        print(f"  action: {action}")
