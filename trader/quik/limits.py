"""Hard order limits — STL side (sprint02 Phase 2).

The SAME limits the QUIK agent enforces, re-checked here BEFORE any order leaves
STL for the agent (defense in depth). A request that fails ANY limit is rejected
at the API and never reaches the agent / Lua / QUIK.

HUMAN-INITIATED only: these limits do not place orders, they only gate operator
placements. No strategy, no auto-trading (Guard 3). Limit values live in config
(env), instrument/account names via keymaster — never hardcoded secrets here.
"""

from __future__ import annotations

from dataclasses import dataclass


class LimitError(Exception):
    """An order request violated a hard limit. The message is the reject reason."""


@dataclass(frozen=True)
class OrderLimits:
    """Snapshot of the hard limits + master flag for one check."""

    trading_enabled: bool
    max_contracts_per_order: int
    max_working_contracts: int
    price_collar_frac: float
    instrument_whitelist: tuple[str, ...]
    daily_order_cap: int

    @classmethod
    def from_settings(cls, settings) -> "OrderLimits":
        wl = tuple(
            s.strip()
            for s in (settings.quik_instrument_whitelist or "").split(",")
            if s.strip()
        )
        return cls(
            trading_enabled=bool(settings.quik_trading_enabled),
            max_contracts_per_order=int(settings.quik_max_contracts_per_order),
            max_working_contracts=int(settings.quik_max_working_contracts),
            price_collar_frac=float(settings.quik_price_collar_frac),
            instrument_whitelist=wl,
            daily_order_cap=int(settings.quik_daily_order_cap),
        )


def check_master_flag(limits: OrderLimits) -> None:
    """Reject everything mutating when the master flag is off."""
    if not limits.trading_enabled:
        raise LimitError(
            "Торговля QUIK отключена (quik_trading_enabled=false). "
            "Заявка отклонена."
        )


def check_whitelist(limits: OrderLimits, code: str) -> None:
    if not code:
        raise LimitError("Инструмент не указан.")
    if code not in limits.instrument_whitelist:
        raise LimitError(
            f"Инструмент {code} не в белом списке "
            f"{list(limits.instrument_whitelist)}."
        )


def check_quantity(limits: OrderLimits, quantity: int) -> None:
    q = int(quantity)
    if q <= 0:
        raise LimitError("Количество должно быть > 0.")
    if q > limits.max_contracts_per_order:
        raise LimitError(
            f"Количество {q} превышает лимит на заявку "
            f"{limits.max_contracts_per_order}."
        )


def check_working(limits: OrderLimits, current_working: int, add_qty: int) -> None:
    """Resting + this order must not exceed max_working_contracts."""
    total = int(current_working) + int(add_qty)
    if total > limits.max_working_contracts:
        raise LimitError(
            f"Суммарный объём в работе {total} превысит лимит "
            f"{limits.max_working_contracts} "
            f"(в работе {current_working} + заявка {add_qty})."
        )


def check_collar(limits: OrderLimits, collar: float) -> None:
    """The order's adverse-deviation collar must be within the configured max."""
    c = float(collar)
    if c < 0:
        raise LimitError("Коллар не может быть отрицательным.")
    # An order may TIGHTEN the collar but never loosen it beyond the hard bound.
    if c > limits.price_collar_frac:
        raise LimitError(
            f"Коллар {c} превышает максимум {limits.price_collar_frac}."
        )


def check_daily_cap(limits: OrderLimits, placed_today: int) -> None:
    if int(placed_today) >= limits.daily_order_cap:
        raise LimitError(
            f"Достигнут дневной лимит заявок "
            f"({placed_today}/{limits.daily_order_cap})."
        )


def validate_place(
    limits: OrderLimits,
    *,
    code: str,
    quantity: int,
    collar: float,
    current_working: int,
    placed_today: int,
) -> None:
    """Run EVERY hard limit for a place request. Raises LimitError on the first fail.

    Order is master flag → whitelist → quantity → working total → collar → daily cap.
    """
    check_master_flag(limits)
    check_whitelist(limits, code)
    check_quantity(limits, quantity)
    check_working(limits, current_working, quantity)
    check_collar(limits, collar)
    check_daily_cap(limits, placed_today)


def validate_start_execution(
    limits: OrderLimits,
    *,
    code: str,
    target_quantity: int,
    current_working: int,
    placed_today: int,
) -> None:
    """Limits for a maker-execution start (1b). Same gates minus the per-order
    collar fraction (execution uses worst_price, bounded by the agent)."""
    check_master_flag(limits)
    check_whitelist(limits, code)
    check_quantity(limits, target_quantity)
    check_working(limits, current_working, target_quantity)
    check_daily_cap(limits, placed_today)
