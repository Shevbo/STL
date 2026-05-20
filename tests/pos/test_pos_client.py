from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from trader.pos.client import PositionsClient
from trader.pos.models import AccountSummary, Position

ACCOUNT_URL = "https://api.finam.ru/v1/accounts/2035452"


@pytest.fixture
def client():
    return PositionsClient(
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="test_token"),
        account_id="2035452",
    )


# Real Finam format: protobuf-style {"value": "..."} for numeric fields.
# Side is derived from quantity sign (negative = short), not a field.
# avg_price is not returned by Finam — always 0.
ACCOUNT_RESPONSE = {
    "equity": {"value": "1793087.28"},
    "unrealized_profit": {"value": "-11344.44"},
    "portfolio_forts": {
        "available_cash": {"value": "169281.99"},
        "money_reserved": {"value": "1636734.23"},
    },
    "positions": [
        {
            "symbol": "GZM6@RTSX",
            "quantity": {"value": "3"},
            "current_price": {"value": "101.00"},
            "unrealized_pnl": {"value": "1.50"},
        },
        {
            "symbol": "SRM6@RTSX",
            "quantity": {"value": "-1"},
            "current_price": {"value": "199.00"},
            "unrealized_pnl": {"value": "1.00"},
        },
    ],
}


@respx.mock
async def test_get_portfolio_returns_positions(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    positions = await client.get_portfolio()

    assert len(positions) == 2
    assert all(isinstance(p, Position) for p in positions)


@respx.mock
async def test_get_portfolio_field_mapping(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    positions = await client.get_portfolio()
    p = positions[0]

    assert p.symbol == "GZM6@RTSX"
    assert p.account_id == "2035452"
    assert p.side == "long"
    assert p.quantity == 3
    assert p.avg_price == Decimal("0")  # not returned by Finam
    assert p.current_price == Decimal("101.00")
    assert p.var_margin == Decimal("1.50")


@respx.mock
async def test_get_portfolio_short_position(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    positions = await client.get_portfolio()
    p = positions[1]

    assert p.side == "short"
    assert p.symbol == "SRM6@RTSX"
    assert p.quantity == 1  # abs value


@respx.mock
async def test_get_portfolio_sends_auth_header(client):
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    await client.get_portfolio()

    assert route.calls[0].request.headers["authorization"] == "Bearer test_token"


@respx.mock
async def test_get_portfolio_uses_account_id_in_path(client):
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    await client.get_portfolio()

    assert "2035452" in str(route.calls[0].request.url)


@respx.mock
async def test_get_portfolio_empty_positions(client):
    body = {**ACCOUNT_RESPONSE, "positions": []}
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=body))
    positions = await client.get_portfolio()

    assert positions == []


@respx.mock
async def test_get_portfolio_no_positions_key(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json={"balance": "100"}))
    positions = await client.get_portfolio()

    assert positions == []


@respx.mock
async def test_get_portfolio_flat_position(client):
    body = {
        **ACCOUNT_RESPONSE,
        "positions": [
            {
                "symbol": "GZM6@RTSX",
                "quantity": {"value": "0"},
                "current_price": {"value": "100.00"},
                "unrealized_pnl": {"value": "0"},
            }
        ],
    }
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=body))
    positions = await client.get_portfolio()

    assert positions[0].side == "flat"
    assert positions[0].quantity == 0
    assert positions[0].var_margin == Decimal("0")


@respx.mock
async def test_get_portfolio_propagates_http_error(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_portfolio()


@respx.mock
async def test_get_portfolio_propagates_503(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(503, text="service unavailable"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_portfolio()


# --- get_account_summary ---

@respx.mock
async def test_get_account_summary_returns_model(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    summary = await client.get_account_summary()

    assert isinstance(summary, AccountSummary)


@respx.mock
async def test_get_account_summary_field_mapping(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    summary = await client.get_account_summary()

    assert summary.deposit == Decimal("1793087.28")
    assert summary.free == Decimal("169281.99")
    assert summary.in_position == Decimal("1636734.23")
    assert summary.variation_margin == Decimal("-11344.44")


@respx.mock
async def test_get_account_summary_sends_auth_header(client):
    route = respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json=ACCOUNT_RESPONSE))
    await client.get_account_summary()

    assert route.calls[0].request.headers["authorization"] == "Bearer test_token"


@respx.mock
async def test_get_account_summary_missing_fields_default_zero(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(200, json={"positions": []}))
    summary = await client.get_account_summary()

    assert summary.deposit == Decimal("0")
    assert summary.free == Decimal("0")
    assert summary.in_position == Decimal("0")
    assert summary.variation_margin == Decimal("0")


@respx.mock
async def test_get_account_summary_propagates_http_error(client):
    respx.get(ACCOUNT_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_account_summary()
