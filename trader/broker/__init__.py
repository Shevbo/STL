"""Broker abstraction layer — robots trade through ONE strict contract.

Public surface:
  * ``base`` — the contract (``BrokerInterface``, data models, ``Capability``).
  * ``registry`` — ``@register`` + ``get_broker(settings)`` factory with the
    trade-ready hard gate.
  * adapters (``finam``, ``quik``) self-register on import via the registry.

Robots and strategies import ONLY ``base`` + ``registry`` — never a concrete
adapter. The concrete broker is chosen at runtime from ``settings.exchange_interface``.
"""

from __future__ import annotations

from trader.broker import base, registry

__all__ = ["base", "registry"]
