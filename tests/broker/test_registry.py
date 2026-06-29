"""Registry selection + the trade-ready HARD GATE.

No network, no orders. A fake PARTIAL adapter is rejected by the gate; a fake FULL
adapter passes; read-only bypass works; unknown names raise.
"""

from __future__ import annotations

import pytest

from trader.broker import registry
from trader.broker.base import CORE_CAPABILITIES, BrokerInterface, Capability
from trader.broker.registry import (
    BrokerNotRegistered,
    BrokerNotTradeReady,
    get_broker,
    register,
)


class _Settings:
    def __init__(self, interface: str) -> None:
        self.exchange_interface = interface


class _FullBroker(BrokerInterface):
    name = "fulltest"

    def __init__(self, settings, **inject):
        self.settings = settings
        self.inject = inject

    def capabilities(self):
        return set(CORE_CAPABILITIES)


class _PartialBroker(BrokerInterface):
    name = "partialtest"

    def __init__(self, settings, **inject):
        self.settings = settings

    def capabilities(self):
        # Missing CANCEL_ORDER + ACCOUNT -> not trade-ready.
        return set(CORE_CAPABILITIES) - {Capability.CANCEL_ORDER, Capability.ACCOUNT}


@pytest.fixture(autouse=True)
def _register_fakes():
    register("fulltest")(lambda s, **k: _FullBroker(s, **k))
    register("partialtest")(lambda s, **k: _PartialBroker(s, **k))
    yield
    registry._REGISTRY.pop("fulltest", None)
    registry._REGISTRY.pop("partialtest", None)


def test_full_adapter_passes_gate():
    broker = get_broker(_Settings("fulltest"))
    assert broker.name == "fulltest"
    assert broker.is_trade_ready()
    assert broker.missing_core() == set()


def test_partial_adapter_rejected_by_gate():
    with pytest.raises(BrokerNotTradeReady) as exc:
        get_broker(_Settings("partialtest"))
    # The error names exactly the missing CORE caps.
    assert exc.value.missing == {Capability.CANCEL_ORDER, Capability.ACCOUNT}
    assert "cancel_order" in str(exc.value)
    assert "account" in str(exc.value)


def test_partial_adapter_allowed_read_only():
    broker = get_broker(_Settings("partialtest"), require_trade_ready=False)
    assert broker.name == "partialtest"
    assert not broker.is_trade_ready()


def test_unknown_interface_raises():
    with pytest.raises(BrokerNotRegistered):
        get_broker(_Settings("does-not-exist"))


def test_empty_interface_raises():
    with pytest.raises(BrokerNotRegistered):
        get_broker(_Settings(""))


def test_inject_forwarded_to_factory():
    broker = get_broker(_Settings("fulltest"), foo="bar")
    assert broker.inject == {"foo": "bar"}


def test_builtin_adapters_registered():
    # Importing the package self-registers finam + quik.
    from trader.broker import finam, quik  # noqa: F401

    names = registry.registered_names()
    assert "finam" in names
    assert "quik" in names
