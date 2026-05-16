# M1 Market Data — gRPC Rewrite Design Spec

**Date:** 2026-05-16  
**Status:** Approved  
**Scope:** Replace WebSocket placeholder in `trader/md/` with typed gRPC client against Finam Trade API

---

## 1. Context

M1 (`trader/md/`) currently ships a WebSocket stub (`ws_client.py`) with `# TODO: Verify from Finam API docs` throughout. Unit tests pass (49 green) but integration is 0/3. The Finam Trade API uses gRPC over TLS on `api.finam.ru:443`. Proto source is vendored from the public repo `github.com/FinamWeb/finam-trade-api`.

---

## 2. Architecture

### 2.1 Component Overview

```
feed.py  ←→  QuoteStream (new)  ←→  grpc.aio.Channel  ←→  Finam gRPC API
              ↑ per-symbol tasks
              fan-in → asyncio.Queue → iter_quotes()
```

**Deleted:** `ws_client.py`, `WsSession`  
**New:** `trader/md/grpc_client.py` (`QuoteStream`)  
**Modified:** `feed.py` (accepts `QuoteStream` instead of `WsSession`)

### 2.2 Proto Vendoring

Vendor all transitive imports. Directory under `trader/proto/`:

```
trader/proto/
  grpc/tradeapi/v1/marketdata/marketdata_service.proto
  grpc/tradeapi/v1/side.proto
  grpc/tradeapi/v1/trade.proto
  google/api/annotations.proto
  google/api/http.proto
  google/protobuf/timestamp.proto
  google/type/decimal.proto
  google/type/interval.proto
  grpc/gateway/protoc_gen_openapiv2/options/annotations.proto
```

Generated stubs land in `trader/proto/gen/` (gitignored except for a regen script). CI checks that stubs are up-to-date via `regen-diff` check.

**Pinned versions (pyproject.toml):**
```toml
grpcio = ">=1.63,<2"
grpcio-tools = ">=1.63,<2"
protobuf = ">=5.26,<6"
```

**Known issue — import-path rewriting:** `grpcio-tools` codegen emits absolute import paths in `_pb2_grpc.py` (e.g. `import grpc.tradeapi.v1.marketdata.marketdata_service_pb2`). The regen script must rewrite these to relative imports (`from . import ...`) after generation.

### 2.3 Key Proto Shapes

```protobuf
service MarketDataService {
  rpc LastQuote(QuoteRequest) returns (QuoteResponse);
  rpc SubscribeQuote(SubscribeQuoteRequest) returns (stream SubscribeQuoteResponse);
}

message SubscribeQuoteRequest { repeated string symbols = 1; }
message SubscribeQuoteResponse {
  repeated Quote quote = 1;
  StreamError error = 2;
}
message Quote {
  string symbol = 1;
  google.protobuf.Timestamp timestamp = 2;
  google.type.Decimal ask = 3;
  google.type.Decimal ask_size = 4;
  google.type.Decimal bid = 5;
  google.type.Decimal bid_size = 6;
  google.type.Decimal last = 7;
  google.type.Decimal last_size = 8;
  google.type.Decimal volume = 9;
  // ... open/high/low/close/change/...
}
message StreamError { int32 code = 1; string description = 2; }
message QuoteRequest { string symbol = 1; }
message QuoteResponse { string symbol = 1; Quote quote = 2; }
```

---

## 3. QuoteStream (`trader/md/grpc_client.py`)

### 3.1 Responsibilities

- Open `grpc.aio.secure_channel` with TLS and keepalive options
- Authenticate via `authorization` metadata key (raw JWT, no `Bearer` prefix)
- Fan-in: one `asyncio.Task` per subscribed symbol, each calling `SubscribeQuote`
- Flatten `repeated Quote` from each `SubscribeQuoteResponse` into the shared queue
- Handle `StreamError` in payload (not RPC-level): log + continue
- Reconnect per-symbol on stream error with full-jitter backoff
- Expose `iter_quotes() → AsyncIterator[dict]` (same contract as old `WsSession`)
- Expose `subscribe(symbol)`, `close()`

### 3.2 Channel Options

```python
channel_options = [
    ("grpc.keepalive_time_ms", 20_000),        # ping every 20s
    ("grpc.keepalive_timeout_ms", 10_000),      # wait 10s for pong
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]
```

### 3.3 Authentication

- Metadata key: `authorization` (lowercase)  
- Value: raw JWT token, no `Bearer` prefix  
- Token refreshed via `get_token: Callable[[], Awaitable[str]]` (injected)  
- `force_refresh` added to `AsyncAuthClient.get_token()` for expired-token recovery during reconnect

### 3.4 Quote Mapping

```python
from decimal import Decimal

def _dec(pb_decimal) -> Decimal:
    return Decimal(pb_decimal.value or "0")

def quote_from_proto(pb: Quote) -> dict:
    return {
        "symbol": pb.symbol,
        "timestamp": pb.timestamp.ToDatetime(tzinfo=timezone.utc),
        "ask": _dec(pb.ask),
        "bid": _dec(pb.bid),
        "last": _dec(pb.last),
        "ask_size": _dec(pb.ask_size),
        "bid_size": _dec(pb.bid_size),
        "volume": _dec(pb.volume),
    }
```

`Quote.from_payload(symbol, raw_dict)` in `trader/md/models.py` remains unchanged; the new client produces the same `dict` shape.

### 3.5 Fan-in Pattern

```python
async def _stream_symbol(self, symbol: str) -> None:
    attempt = 0
    while self._running:
        try:
            token = await self._get_token()
            metadata = [("authorization", token)]
            stub = MarketDataServiceStub(self._channel)
            req = SubscribeQuoteRequest(symbols=[symbol])
            async for resp in stub.SubscribeQuote(req, metadata=metadata):
                if resp.HasField("error") and resp.error.code:
                    log.warning("md.stream_error", symbol=symbol, code=resp.error.code)
                    continue
                for pb_quote in resp.quote:
                    self._put_data(quote_from_proto(pb_quote))
            attempt = 0  # clean exit — reconnect immediately
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAUTHENTICATED:
                # force-refresh token then retry once
                ...
            delay = _backoff(attempt)
            await asyncio.sleep(delay)
            attempt += 1
```

### 3.6 Spike (Task #1 in Implementation Plan)

Before full implementation, a spike validates connectivity:
1. Open channel to `api.finam.ru:443` with TLS
2. Call `LastQuote(QuoteRequest(symbol="SBER@MISX"))` with real JWT from `.shectory_trade.env`
3. Print response or gRPC status code

This confirms: correct host/port, TLS handshake, metadata format, and proto compatibility. No reconnect/fan-in logic involved.

---

## 4. `feed.py` Changes

`MarketDataFeed.__init__` changes from `ws: WsSession` to `qs: QuoteStream`. All other logic (`QuoteState`, `_reader`, `_watchdog`, `subscribe`, `aclose`) stays unchanged — `QuoteStream` exposes the same `iter_quotes()` / `subscribe()` / `close()` contract.

`feed.py` does not import anything from `grpc_client.py` directly in non-integration paths, so unit tests remain mockable.

---

## 5. Testing

### 5.1 Unit Tests

- `tests/md/test_grpc_client.py`: use real generated proto objects (not MagicMock)
  - Build `SubscribeQuoteResponse` with `Quote` fields populated
  - Mock `stub.SubscribeQuote` as async generator
  - Assert `quote_from_proto` maps fields correctly
  - Assert reconnect backoff on `AioRpcError`
- `tests/md/test_feed.py`: unchanged; `MarketDataFeed` is injected with a `QuoteStream`-compatible mock
- `tests/md/test_reconnect.py`: adapted to inject fake `QuoteStream` instead of `WsSession`

### 5.2 Integration Tests

- `tests/md/test_md_integration.py`: real JWT, real symbol `SBER@MISX`, assert `Quote` arrives within 10s
- Marked `@pytest.mark.integration`, skipped without `FINAM_SECRET_TOKEN`

---

## 6. CI / Regen Check

```bash
# scripts/regen_proto.sh
cd trader/proto
python -m grpc_tools.protoc \
  -I. \
  --python_out=gen \
  --grpc_python_out=gen \
  grpc/tradeapi/v1/marketdata/marketdata_service.proto

# Rewrite absolute imports to relative
python scripts/fix_grpc_imports.py trader/proto/gen/

# CI check:
# git diff --exit-code trader/proto/gen/
```

---

## 7. Migration Steps

1. **Spike** — `LastQuote` against live API (validates connectivity, no reconn logic)
2. **Proto vendor + codegen** — regen script + import fixer + CI check
3. **`quote_from_proto` + unit tests** — `Decimal` mapping, green before stream code
4. **`QuoteStream`** — channel, auth metadata, fan-in, `iter_quotes()`, backoff
5. **`feed.py` adapter** — swap `WsSession` → `QuoteStream`
6. **Integration test** — live `SBER@MISX` quote under 10s
7. **Delete `ws_client.py`** — remove WebSocket stub and its tests
8. **pyproject.toml** — remove `websockets`, `orjson`; add `grpcio`, `grpcio-tools`, `protobuf`

---

## 8. Out of Scope

- OrderBook, LatestTrades, Bars streams (future M1 extensions)
- Unsubscribe (gRPC streams are cancelled by closing the task)
- Order execution (M2+)
