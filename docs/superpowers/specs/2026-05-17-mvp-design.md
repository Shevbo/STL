# Shectory Trader — Full MVP Design Spec

**Date:** 2026-05-17
**Status:** Approved
**Scope:** M1 completion + FastAPI layer (M8) + M2 TX Adapter + M5 Positions + Frontend additions

---

## 1. Goal

First working version: browser shows live quotes, user can place a real market order (with confirmation), and see current positions on FORTS ММВБ via Finam Trade API.

**Out of scope:** HFT robots (M3 OMS, M6 Risk Gate, M8 Trader API). Architecture must not block them — Protocols used throughout.

---

## 2. Architecture

```
Browser (Svelte 5)
  │  WebSocket /ws        → котировки, position_update
  │  POST /api/v1/orders  → выставить заявку
  │  GET  /api/v1/portfolio → позиции
  ▼
FastAPI (trader/api/)
  ├── lifespan: Auth + QuoteStream + MarketDataFeed + WsHub + TxClient + PositionsClient
  ├── /ws               — WsHub fan-out quote-тиков (per-client Queue, conflation)
  ├── POST /api/v1/orders — TxClient.place_order()
  └── GET  /api/v1/portfolio — PositionsClient.get_portfolio()
       │
       ├── MarketDataFeed → QuoteStream → grpc.aio → api.finam.ru:443
       ├── TxClient (REST) → POST api.finam.ru/v1/orders
       └── PositionsClient (REST) → GET api.finam.ru/v1/portfolio
```

**New files:**
```
trader/
  api/
    __init__.py
    app.py          # FastAPI + lifespan + routes
    ws_hub.py       # WsHub: fan-out per-client Queue
  tx/
    __init__.py
    client.py       # TxClient (REST, httpx)
    models.py       # OrderRequest, OrderResponse
  pos/
    __init__.py
    client.py       # PositionsClient (REST, httpx)
    models.py       # Position
frontend/src/
  components/
    OrderPanel.svelte
    OrderConfirmDialog.svelte
    PositionsTable.svelte
  lib/
    api.ts          # placeOrder(), fetchPortfolio()
```

**Modified:**
- `trader/md/feed.py` — swap WsSession → QuoteStream via MarketDataSource Protocol
- `tests/md/test_feed.py` — make_mock_ws → make_mock_qs
- `pyproject.toml` — add fastapi, uvicorn[standard], grpcio, protobuf; remove websockets, orjson
- `frontend/src/lib/types.ts` — add Position, OrderRequest, OrderResponse
- `frontend/src/App.svelte` — embed OrderPanel, PositionsTable, OrderConfirmDialog

**Deleted:**
- `trader/md/ws_client.py`
- `tests/md/test_ws_client.py`
- `tests/md/test_reconnect.py`

---

## 3. M1 Completion — feed.py adapter

### 3.1 MarketDataSource Protocol

```python
# trader/md/source.py
from typing import Protocol, runtime_checkable
from collections.abc import AsyncIterator, Callable, Awaitable

@runtime_checkable
class MarketDataSource(Protocol):
    async def start(self, get_token: Callable[[bool], Awaitable[str]]) -> None: ...
    async def subscribe(self, symbol: str) -> None: ...
    def iter_quotes(self) -> AsyncIterator[dict]: ...
    async def close(self) -> None: ...
```

### 3.2 feed.py changes

- `MarketDataFeed.__init__(self, qs: MarketDataSource, ...)` — rename `ws` → `qs`
- `start()`: `await self._qs.start(get_token)` then `await self._qs.subscribe(symbol)` per active symbol — no `on_reconnect` callback (reconnect is internal to QuoteStream)
- `add_symbol()`: `await self._qs.subscribe(symbol)` — auto-reconnects internally
- `aclose()`: `await self._qs.close()` — no `code=1000` arg
- Remove `_resubscribe_all()` entirely
- Remove `import orjson`

### 3.3 Token signature

Unified: `get_token(force_refresh: bool = False) -> Awaitable[str]`

---

## 4. FastAPI App (trader/api/app.py)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    auth = AsyncAuthClient(...)
    qs = QuoteStream()
    await qs.start(get_token=auth.get_token)
    feed = MarketDataFeed(qs=qs)
    hub = WsHub(feed)
    await hub.start(symbols=[settings.finam_mvp_symbol])
    app.state.hub = hub
    app.state.tx = TxClient(base_url=settings.finam_api_base_url, get_token=auth.get_token, account_id=settings.finam_account_id)
    app.state.pos = PositionsClient(base_url=settings.finam_api_base_url, get_token=auth.get_token, account_id=settings.finam_account_id)
    yield
    await hub.stop()
    await auth.aclose()

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await app.state.hub.connect(websocket)

@app.post("/api/v1/orders", response_model=OrderResponse)
async def place_order(body: OrderRequest):
    return await app.state.tx.place_order(body)

@app.get("/api/v1/portfolio", response_model=list[Position])
async def get_portfolio():
    return await app.state.pos.get_portfolio()
```

**Degraded startup:** if `QuoteStream.start()` raises, log error and continue — FastAPI still serves health + orders. WsHub emits `{"type": "service_status", "service": "md", "status": "error"}`.

---

## 5. WsHub (trader/api/ws_hub.py)

- One broadcast Task reads from `feed.subscribe(symbol)` event loop
- Per-client `asyncio.Queue(maxsize=50)` with conflation on overflow (drop oldest, same pattern as `grpc_client._put_data`)
- Separate sender Task per client drains its Queue → `websocket.send_json()`
- On client disconnect: cancel sender Task, remove Queue — does not affect broadcast

**WS messages emitted:**
```jsonc
{"type": "quote", "symbol": "GZM6@RTSX", "bid": 100.5, "ask": 100.6, "last": 100.55, "bid_size": 10, "ask_size": 5, "last_size": 3, "timestamp": "2026-05-17T...Z"}
{"type": "position_update", "positions": [...]}
{"type": "service_status", "service": "md", "status": "ok"}
```

**Position polling:** WsHub polls `PositionsClient.get_portfolio()` every 5s and broadcasts `position_update`. Finam does not stream positions — polling is the only option for MVP.

---

## 6. M2 TX Adapter (trader/tx/)

### 6.1 TxClient

```python
class TxClient:
    async def place_order(self, req: OrderRequest) -> OrderResponse:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        # Marketable limit: price = best ask (buy) or best bid (sell)
        # Caller provides price from current quote
        body = {
            "account_id": self._account_id,
            "client_order_id": req.client_order_id,  # idempotency
            "symbol": req.symbol,
            "side": req.side,
            "quantity": req.quantity,
            "order_type": req.order_type,  # "limit" | "market"
            "price": str(req.price) if req.price else None,
        }
        resp = await self._http.post("/v1/orders", json=body, headers=headers)
        resp.raise_for_status()
        return OrderResponse(**resp.json())
```

### 6.2 Models

```python
class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["limit", "market"] = "limit"
    price: Decimal | None = None        # None only for market orders
    client_order_id: str = Field(default_factory=lambda: str(uuid4()))

class OrderResponse(BaseModel):
    order_id: str
    status: str
```

**Note:** price must be rounded to instrument tick size before submission. `InstrumentRegistry` provides `TradingParams.min_step`. TxClient receives pre-rounded price from caller (FastAPI route validates via Pydantic, rounding is frontend responsibility for MVP).

**HFT extensibility:** `TxClient` implements `TxAdapter` Protocol — future gRPC-based implementation drops in without changing routes.

### 6.3 Paths to verify against real Finam API

- `POST /v1/orders` — endpoint path, request/response field names
- `side` values: `"buy"/"sell"` or `"Buy"/"Sell"` or enum int
- Error response shape for rejection (price out of range, insufficient funds)

---

## 7. M5 Positions (trader/pos/)

### 7.1 PositionsClient

```python
class PositionsClient:
    async def get_portfolio(self) -> list[Position]:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._http.get(
            "/v1/portfolio",
            params={"account_id": self._account_id},
            headers=headers,
        )
        resp.raise_for_status()
        return [Position(**p) for p in resp.json()["positions"]]
```

### 7.2 Position model

```python
class Position(BaseModel):
    symbol: str
    account_id: str
    side: Literal["long", "short", "flat"]
    quantity: int            # absolute value; sign encoded in side
    avg_price: Decimal
    current_price: Decimal
    var_margin: Decimal      # variation margin (futures), not unrealized PnL
```

**Note:** Finam returns variation margin for futures, not classic unrealized PnL. For FORTS this is correct.

### 7.3 Paths to verify

- `GET /v1/portfolio` — params, response shape (`positions` array key, field names)

---

## 8. Frontend Additions

### 8.1 New types (types.ts)

```typescript
export interface Position {
  symbol: string;
  account_id: string;
  side: 'long' | 'short' | 'flat';
  quantity: number;
  avg_price: number;
  current_price: number;
  var_margin: number;
}

export interface OrderRequest {
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  order_type: 'limit' | 'market';
  price?: number;
}

export interface OrderResponse {
  order_id: string;
  status: string;
}
```

### 8.2 New WS message type (types.ts)

```typescript
// добавить в WsIncoming union:
| { type: 'position_update'; positions: Position[] }
```

### 8.3 Components

**OrderPanel.svelte:** Форма (symbol, side toggle buy/sell, quantity, цена авто-заполняется из текущего bid/ask). Кнопка "Отправить" → показывает `OrderConfirmDialog`.

**OrderConfirmDialog.svelte:** Модальное: "SBER · BUY · 1 лот · ~100.50". Кнопки "Подтвердить" / "Отмена". Подтверждение → `api.placeOrder()` → тост с order_id.

**PositionsTable.svelte:** Таблица: Symbol | Side | Qty | Avg Price | Current | Var Margin. Обновляется от `position_update` WS-сообщений.

### 8.4 api.ts additions

```typescript
export async function placeOrder(req: OrderRequest): Promise<OrderResponse> {
  const resp = await fetch('/api/v1/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

export async function fetchPortfolio(): Promise<Position[]> {
  const resp = await fetch('/api/v1/portfolio');
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}
```

---

## 9. Testing Strategy

### Unit tests (no credentials, --timeout=30)
- `tests/md/test_feed.py` — updated: `make_mock_qs` (implements MarketDataSource Protocol), same test cases
- `tests/tx/test_tx_client.py` — mock httpx, test place_order, idempotency key generation, error propagation
- `tests/pos/test_pos_client.py` — mock httpx, test get_portfolio field mapping

### Integration tests (need FINAM_SECRET_TOKEN, @pytest.mark.integration)
- `tests/md/test_md_integration.py` — live quote from GZM6@RTSX within 10s (already planned)
- `tests/tx/test_tx_integration.py` — place real limit order far from market, cancel immediately
- `tests/pos/test_pos_integration.py` — assert portfolio response parses correctly

### No automated frontend tests for MVP — manual verification in browser.

---

## 10. pyproject.toml changes

**Add:**
```toml
fastapi = "^0.111"
uvicorn = {extras = ["standard"], version = "^0.30"}
grpcio = ">=1.63,<2"
protobuf = ">=5.26,<6"
googleapis-common-protos = ">=1.63"
```

**Remove:** `websockets`, `orjson`

**Dev add:** `grpcio-tools = ">=1.63,<2"` (already present)

---

## 11. HFT Extensibility Constraints

The following design decisions ensure HFT robots (M3 OMS, M6 Risk Gate) can be added later without rearchitecting:

1. `MarketDataSource` Protocol — QuoteStream is one implementation; future: direct gRPC event pump for robots
2. `TxAdapter` Protocol — TxClient (REST) is one implementation; future: gRPC OrdersService for sub-10ms order submission
3. Quote events from `QuoteStream` reach robot event loops **server-side** — no WS round-trip in the hot path
4. `WsHub` is display-only; robots subscribe to `feed.subscribe()` directly

---

## 12. Finam API Paths (to verify during implementation)

| Action | Method | Path | Notes |
|--------|--------|------|-------|
| Place order | POST | `/v1/orders` | field names TBD |
| Get portfolio | GET | `/v1/portfolio` | params TBD |
| Cancel order | DELETE | `/v1/orders/{id}` | future |
