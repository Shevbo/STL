"""Валидация лимитов при взведении умной заявки.

Инцидент: оператор взвёл заявку на 50 контрактов при лимите на заявку 34.
Лимиты проверялись только в момент срабатывания и молча помечали заявку
status="error". Теперь заведомо невыполнимая заявка отклоняется при создании.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from trader.api import quik_smart_orders as qso


def _settings(**over):
    base = dict(
        quik_trading_enabled=True,
        quik_max_contracts_per_order=34,
        quik_max_working_contracts=1000,
        quik_price_collar_frac=0.002,
        quik_instrument_whitelist="RIU6",
        quik_daily_order_cap=500,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _req(settings):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


def _body(qty):
    return qso.SmartOrderBody(kind="sl", code="RIU6", side="buy", qty=qty,
                              trigger_price=84770.0)


async def test_create_rejects_qty_over_per_order_cap(monkeypatch):
    monkeypatch.setattr(qso, "_auth", lambda r: None)
    monkeypatch.setattr(qso, "_book", lambda r: SimpleNamespace())
    with pytest.raises(HTTPException) as ei:
        await qso.create(_body(50), _req(_settings()))
    assert ei.value.status_code == 422
    assert "превышает лимит на заявку 34" in ei.value.detail


async def test_create_allows_qty_within_cap(monkeypatch):
    monkeypatch.setattr(qso, "_auth", lambda r: None)
    book = SimpleNamespace(active=lambda: [], add=lambda so: None, save=lambda: None)
    monkeypatch.setattr(qso, "_book", lambda r: book)
    resp = await qso.create(_body(10), _req(_settings()))
    assert resp["ok"] is True


async def test_create_rejects_instrument_not_whitelisted(monkeypatch):
    monkeypatch.setattr(qso, "_auth", lambda r: None)
    monkeypatch.setattr(qso, "_book", lambda r: SimpleNamespace())
    body = qso.SmartOrderBody(kind="sl", code="BRU6", side="buy", qty=1,
                              trigger_price=84770.0)
    with pytest.raises(HTTPException) as ei:
        await qso.create(body, _req(_settings()))
    assert ei.value.status_code == 422
    assert "не в белом списке" in ei.value.detail
