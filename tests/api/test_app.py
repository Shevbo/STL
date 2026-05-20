from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from trader.api.app import create_app
from trader.pos.models import Position
from trader.tx.models import OrderResponse


@pytest.fixture
def mock_tx():
    m = AsyncMock()
    m.place_order.return_value = OrderResponse(order_id="ord-abc", status="accepted")
    return m


@pytest.fixture
def mock_pos():
    m = AsyncMock()
    m.get_portfolio.return_value = []
    return m


@pytest.fixture
async def client(mock_tx, mock_pos):
    app = create_app()
    app.state.tx = mock_tx
    app.state.pos = mock_pos
    app.state.settings = SimpleNamespace(shectory_auth_bridge_secret="")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- POST /api/v1/orders ---

async def test_place_order_returns_200(client, mock_tx):
    resp = await client.post("/api/v1/orders", json={
        "symbol": "GZM6@RTSX",
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "price": "100.50",
    })
    assert resp.status_code == 200
    assert resp.json()["order_id"] == "ord-abc"
    assert resp.json()["status"] == "accepted"


async def test_place_order_calls_tx_client(client, mock_tx):
    await client.post("/api/v1/orders", json={
        "symbol": "GZM6@RTSX",
        "side": "sell",
        "quantity": 2,
        "order_type": "limit",
        "price": "99.00",
    })
    mock_tx.place_order.assert_called_once()
    req = mock_tx.place_order.call_args[0][0]
    assert req.symbol == "GZM6@RTSX"
    assert req.side == "sell"
    assert req.quantity == 2


async def test_place_order_market_no_price(client, mock_tx):
    resp = await client.post("/api/v1/orders", json={
        "symbol": "GZM6@RTSX",
        "side": "buy",
        "quantity": 1,
        "order_type": "market",
    })
    assert resp.status_code == 200
    req = mock_tx.place_order.call_args[0][0]
    assert req.order_type == "market"
    assert req.price is None


async def test_place_order_invalid_side_returns_422(client):
    resp = await client.post("/api/v1/orders", json={
        "symbol": "GZM6@RTSX",
        "side": "hold",
        "quantity": 1,
        "order_type": "limit",
        "price": "100.00",
    })
    assert resp.status_code == 422


async def test_place_order_missing_symbol_returns_422(client):
    resp = await client.post("/api/v1/orders", json={
        "side": "buy",
        "quantity": 1,
    })
    assert resp.status_code == 422


async def test_place_order_tx_error_propagates(client, mock_tx):
    mock_tx.place_order.side_effect = Exception("downstream error")
    resp = await client.post("/api/v1/orders", json={
        "symbol": "GZM6@RTSX",
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "price": "100.00",
    })
    assert resp.status_code == 500


# --- GET /api/v1/portfolio ---

async def test_get_portfolio_returns_200_empty(client):
    resp = await client.get("/api/v1/portfolio")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_portfolio_returns_positions(client, mock_pos):
    mock_pos.get_portfolio.return_value = [
        Position(
            symbol="GZM6@RTSX",
            account_id="2035452",
            side="long",
            quantity=3,
            avg_price=Decimal("100.50"),
            current_price=Decimal("101.00"),
            var_margin=Decimal("1.50"),
        )
    ]
    resp = await client.get("/api/v1/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "GZM6@RTSX"
    assert data[0]["side"] == "long"
    assert data[0]["quantity"] == 3


async def test_get_portfolio_calls_pos_client(client, mock_pos):
    await client.get("/api/v1/portfolio")
    mock_pos.get_portfolio.assert_called_once()


async def test_get_portfolio_pos_error_propagates(client, mock_pos):
    mock_pos.get_portfolio.side_effect = Exception("pos error")
    resp = await client.get("/api/v1/portfolio")
    assert resp.status_code == 500
