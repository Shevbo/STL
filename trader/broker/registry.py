"""Broker registry + factory — the ONLY place that knows concrete adapters.

Adapters self-register with ``@register("name")``. ``get_broker(settings)`` picks
the adapter by ``settings.exchange_interface`` and (by default) refuses to hand a
partial adapter to a robot for live trading: the HARD GATE checks
``is_trade_ready()`` and raises ``BrokerNotTradeReady`` naming the missing CORE
capabilities. Read-only / design use may bypass the gate with
``require_trade_ready=False``.

No adapter is imported by robots directly. Robots depend only on
``trader.broker.base`` + this factory. Adapter config (endpoints, account,
tokens) comes from ``settings`` / keymaster BY NAME — never hardcoded here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trader.broker.base import BrokerInterface, Capability

# name -> factory(settings, **kwargs) -> BrokerInterface
_REGISTRY: dict[str, Callable[..., BrokerInterface]] = {}


class BrokerNotRegistered(KeyError):
    """Raised when settings select an adapter name no adapter registered."""

    def __init__(self, name: str, known: list[str]) -> None:
        known_str = ", ".join(sorted(known)) or "(none)"
        super().__init__(
            f"no broker adapter registered for exchange_interface={name!r}; "
            f"known adapters: {known_str}"
        )
        self.name = name
        self.known = known


class BrokerNotTradeReady(RuntimeError):
    """Raised by the HARD GATE when a selected adapter is missing CORE capabilities.

    A partial adapter must never be handed to a robot for live trading. The error
    names exactly which CORE caps are absent so the operator/sub-agent knows what
    is still needed (e.g. QuikBroker until the agent reports positions/account and
    native MOVE_ORDERS lands).
    """

    def __init__(self, name: str, missing: set[Capability]) -> None:
        missing_str = ", ".join(sorted(c.value for c in missing))
        super().__init__(
            f"broker adapter {name!r} is not trade-ready: missing CORE "
            f"capabilities [{missing_str}]. Use require_trade_ready=False only for "
            f"read-only / design use."
        )
        self.name = name
        self.missing = missing


def register(name: str) -> Callable[[Callable[..., BrokerInterface]], Callable[..., BrokerInterface]]:
    """Decorator: register a broker factory (usually the adapter class) under ``name``.

    The decorated callable takes ``settings`` (and optional keyword injections) and
    returns a constructed ``BrokerInterface``. The class itself is returned
    unchanged so it stays usable/importable for tests.
    """

    def _decorator(factory: Callable[..., BrokerInterface]) -> Callable[..., BrokerInterface]:
        key = name.strip().lower()
        if not key:
            raise ValueError("broker adapter name must be non-empty")
        _REGISTRY[key] = factory
        return factory

    return _decorator


def registered_names() -> list[str]:
    """Adapter names currently registered (after the adapters are imported)."""
    return sorted(_REGISTRY)


def _load_builtin_adapters() -> None:
    """Import the built-in adapters so their @register decorators run.

    Imported lazily (inside get_broker) so importing the registry alone never
    pulls heavy adapter deps, and so an adapter import error surfaces only when
    that adapter is actually selected.
    """
    # finam.py / quik.py call register(...) at import time.
    from trader.broker import finam  # noqa: F401
    from trader.broker import quik  # noqa: F401


def get_broker(
    settings: Any,
    *,
    require_trade_ready: bool = True,
    **inject: Any,
) -> BrokerInterface:
    """Pick + construct the broker adapter for ``settings.exchange_interface``.

    HARD GATE: when ``require_trade_ready`` (the default), a selected adapter that
    is not ``is_trade_ready()`` raises ``BrokerNotTradeReady`` listing the missing
    CORE caps — a partial adapter can never reach a robot for live trading.

    ``inject`` forwards adapter-specific dependencies (e.g. the QUIK stores/server
    on app.state) to the factory for in-process construction + testability.
    """
    name = getattr(settings, "exchange_interface", "") or ""
    key = name.strip().lower()
    if not key:
        raise BrokerNotRegistered(name, registered_names())

    if key not in _REGISTRY:
        _load_builtin_adapters()
    if key not in _REGISTRY:
        raise BrokerNotRegistered(name, registered_names())

    broker = _REGISTRY[key](settings, **inject)

    if require_trade_ready and not broker.is_trade_ready():
        raise BrokerNotTradeReady(broker.name, broker.missing_core())
    return broker
