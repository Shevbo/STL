import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from trader.tx.client import TxClient
from trader.tx.models import OrderRequest, OrderResponse

# Real Finam endpoint: account_id is in URL path, not body.
# Body uses protobuf-style fields: quantity={"value":"N"}, limit_price={"value":"N.N"},
# side="SIDE_BUY"|"SIDE_SELL", type="ORDER_TYPE_LIMIT"|"ORDER_TYPE_MARKET".
ORDERS_URL = "https://api.finam.ru/v1/accounts/2035452/orders/"
ORDER_RESPONSE = {"order_id": "ord-123", "status": "accepted"}


@pytest.fixture
def client():
    return TxClient(
        base_url="https://api.finam.ru",
        get_token=AsyncMock(return_value="test_token"),
        account_id="2035452",
    )


@respx.mock
async def test_place_limit_order_success(client):
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(
        symbol="GZM6@RTSX",
        side="buy",
        quantity=1,
        order_type="limit",
        price=Decimal("100.50"),
    )
    resp = await client.place_order(req)

    assert isinstance(resp, OrderResponse)
    assert resp.order_id == "ord-123"
    assert resp.status == "accepted"
    assert route.called


@respx.mock
async def test_place_order_sends_auth_header(client):
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="sell", quantity=2, order_type="limit", price=Decimal("99.00"))
    await client.place_order(req)

    assert route.calls[0].request.headers["authorization"] == "Bearer test_token"


@respx.mock
async def test_place_order_sends_account_id(client):
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.00"))
    await client.place_order(req)

    assert "2035452" in str(route.calls[0].request.url)


@respx.mock
async def test_place_market_order_rejected(client):
    # Market orders are disallowed — only limit (taker) orders are permitted.
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="market")
    with pytest.raises(ValueError):
        await client.place_order(req)
    assert route.call_count == 0  # never reaches the broker


@respx.mock
async def test_place_order_client_order_id_generated(client):
    respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.00"))
    assert req.client_order_id != ""


@respx.mock
async def test_place_order_idempotency_key_stable(client):
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.00"))
    cid = req.client_order_id

    respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    await client.place_order(req)

    route = respx.routes[-1]
    body = json.loads(route.calls[0].request.content)
    assert body["client_order_id"] == cid


@respx.mock
async def test_place_order_two_requests_have_different_ids(client):
    req1 = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.00"))
    req2 = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.00"))
    assert req1.client_order_id != req2.client_order_id


@respx.mock
async def test_place_order_propagates_http_error(client):
    respx.post(ORDERS_URL).mock(return_value=httpx.Response(400, json={"message": "price out of range"}))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("1.00"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.place_order(req)


@respx.mock
async def test_place_order_propagates_503(client):
    respx.post(ORDERS_URL).mock(return_value=httpx.Response(503, text="service unavailable"))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.00"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.place_order(req)


@respx.mock
async def test_place_order_price_sent_as_protobuf_value(client):
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=1, order_type="limit", price=Decimal("100.50"))
    await client.place_order(req)

    body = json.loads(route.calls[0].request.content)
    assert "limit_price" in body
    assert body["limit_price"]["value"] == "100.5"


@respx.mock
async def test_place_order_side_mapped_to_finam_enum(client):
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="sell", quantity=1, order_type="limit", price=Decimal("100.00"))
    await client.place_order(req)

    body = json.loads(route.calls[0].request.content)
    assert body["side"] == "SIDE_SELL"


@respx.mock
async def test_place_order_quantity_sent_as_protobuf_value(client):
    route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json=ORDER_RESPONSE))
    req = OrderRequest(symbol="GZM6@RTSX", side="buy", quantity=5, order_type="limit", price=Decimal("100.00"))
    await client.place_order(req)

    body = json.loads(route.calls[0].request.content)
    assert body["quantity"] == {"value": "5"}
