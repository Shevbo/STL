from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from trader.registry.client import InstrumentRegistry


@pytest.fixture
def registry():
    return InstrumentRegistry(
        base_url="https://api.finam.ru",
        get_token=lambda: "test_token",
    )


PAGE_1 = {
    "assets": [
        {"symbol": "GZM6@RFUD", "ticker": "GZM6", "mic": "RFUD",
         "name": "Газпром-6.26", "type": "future", "is_archived": False},
        {"symbol": "SBER@MISX", "ticker": "SBER", "mic": "MISX",
         "name": "Сбербанк", "type": "stock", "is_archived": False},
    ],
    "next_cursor": 999,
}

PAGE_2 = {
    "assets": [
        {"symbol": "GZU6@RFUD", "ticker": "GZU6", "mic": "RFUD",
         "name": "Газпром-9.26", "type": "future", "is_archived": False},
    ],
    "next_cursor": 0,
}


@respx.mock
async def test_search_paginates_and_filters(registry):
    respx.get("https://api.finam.ru/v1/assets/all").mock(
        side_effect=[
            httpx.Response(200, json=PAGE_1),
            httpx.Response(200, json=PAGE_2),
        ]
    )
    results = await registry.search("GZM6")
    assert len(results) == 1
    assert results[0].symbol == "GZM6@RFUD"
    assert results[0].ticker == "GZM6"


@respx.mock
async def test_search_empty_result(registry):
    respx.get("https://api.finam.ru/v1/assets/all").mock(
        return_value=httpx.Response(200, json={"assets": [], "next_cursor": 0})
    )
    results = await registry.search("UNKNOWN")
    assert results == []


@respx.mock
async def test_search_uses_cache_on_second_call(registry):
    route = respx.get("https://api.finam.ru/v1/assets/all").mock(
        return_value=httpx.Response(200, json={"assets": PAGE_1["assets"], "next_cursor": 0})
    )
    await registry.search("GZM6")
    await registry.search("SBER")
    assert route.call_count == 1


DETAIL_RESPONSE = {
    "ticker": "GZM6", "mic": "RFUD", "name": "Газпром-6.26",
    "type": "future", "is_archived": False,
    "decimals": 0, "min_step": 10,
    "lot_size": {"value": "1"},
    "quote_currency": "RUB",
    "future_details": {
        "expiration_date": "2026-06-19T00:00:00Z",
        "contract_size": {"value": "100"},
    },
}


@respx.mock
async def test_get_detail_parses_response(registry):
    respx.get("https://api.finam.ru/v1/assets/GZM6@RFUD").mock(
        return_value=httpx.Response(200, json=DETAIL_RESPONSE)
    )
    detail = await registry.get_detail("GZM6@RFUD", account_id="2035452")
    assert detail.symbol == "GZM6@RFUD"
    assert detail.ticker == "GZM6"
    assert detail.mic == "RFUD"
    assert detail.lot_size == Decimal("1")
    assert detail.min_step == Decimal("10")  # 10 / 10^0 = 10
    assert detail.expiration_date == date(2026, 6, 19)
    assert detail.quote_currency == "RUB"


PARAMS_RESPONSE = {
    "symbol": "GZM6@RFUD",
    "account_id": "2035452",
    "is_tradable": {"value": True},
    "long_initial_margin": {"currency_code": "RUB", "units": "5000", "nanos": 0},
    "short_initial_margin": {"currency_code": "RUB", "units": "4800", "nanos": 500000000},
}


@respx.mock
async def test_get_params_parses_tradable_and_margins(registry):
    respx.get("https://api.finam.ru/v1/assets/GZM6@RFUD/params").mock(
        return_value=httpx.Response(200, json=PARAMS_RESPONSE)
    )
    params = await registry.get_params("GZM6@RFUD", account_id="2035452")
    assert params.symbol == "GZM6@RFUD"
    assert params.is_tradable is True
    assert params.long_initial_margin == Decimal("5000")
    assert params.short_initial_margin == Decimal("4800.5")


@respx.mock
async def test_get_params_not_tradable_when_null(registry):
    respx.get("https://api.finam.ru/v1/assets/GZM6@RFUD/params").mock(
        return_value=httpx.Response(200, json={
            "symbol": "GZM6@RFUD",
            "is_tradable": None,
            "long_initial_margin": {"currency_code": "RUB", "units": "0", "nanos": 0},
            "short_initial_margin": {"currency_code": "RUB", "units": "0", "nanos": 0},
        })
    )
    params = await registry.get_params("GZM6@RFUD", account_id="2035452")
    assert params.is_tradable is False
