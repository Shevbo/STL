# M1 gRPC Market Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the WebSocket stub in `trader/md/` with a typed gRPC streaming client against the real Finam Trade API.

**Architecture:** Fan-in pattern — one `asyncio.Task` per subscribed symbol, each calling `SubscribeQuote` on a persistent `grpc.aio.secure_channel`; all quote dicts land in a shared `asyncio.Queue` consumed by `feed.py`'s `_reader` loop unchanged. `QuoteStream` replaces `WsSession` with the same `start / subscribe / iter_quotes / close` interface.

**Tech Stack:** `grpcio>=1.63`, `grpcio-tools>=1.63`, `protobuf>=5.26`, `googleapis-common-protos` (provides `google.type`, `google.api` at runtime). Python 3.12, `grpc.aio`, `asyncio`. TDD throughout.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `trader/proto/grpc/tradeapi/v1/marketdata/marketdata_service.proto` | Vendored Finam proto |
| Create | `trader/proto/grpc/tradeapi/v1/side.proto` | Vendored side enum |
| Create | `trader/proto/grpc/gateway/protoc_gen_openapiv2/options/annotations.proto` | Gateway annotation (import only) |
| Create | `trader/proto/google/api/annotations.proto` | Google API annotation |
| Create | `trader/proto/google/api/http.proto` | HTTP rule proto |
| Create | `trader/proto/google/type/decimal.proto` | Decimal message |
| Create | `trader/proto/google/type/interval.proto` | Interval message |
| Create | `trader/proto/gen/` (gitignored) | Generated Python stubs |
| Create | `scripts/regen_proto.sh` | Codegen + import fix |
| Create | `scripts/fix_grpc_imports.py` | Rewrite conflicting `grpc.*` imports in `_pb2_grpc.py` |
| Create | `scripts/spike_last_quote.py` | One-shot connectivity spike |
| Create | `trader/md/grpc_client.py` | `QuoteStream` + `quote_from_proto` |
| Create | `tests/md/test_grpc_client.py` | Unit tests for `QuoteStream` |
| Modify | `trader/auth/client.py` | Add `force_refresh: bool = False` to `get_token()` |
| Modify | `tests/auth/test_auth_client.py` | Cover `force_refresh` |
| Modify | `trader/md/feed.py` | Swap `WsSession` → `QuoteStream` injection |
| Modify | `tests/md/test_feed.py` | Replace `make_mock_ws` → `make_mock_qs` |
| Modify | `tests/md/test_integration.py` | Use `QuoteStream` instead of `WsSession` |
| Modify | `pyproject.toml` | Remove websockets/orjson, add grpcio/protobuf/googleapis |
| Modify | `.gitignore` | Ignore `trader/proto/gen/` |
| Delete | `trader/md/ws_client.py` | WebSocket stub — replaced |
| Delete | `tests/md/test_ws_client.py` | Tests for deleted WsSession |
| Delete | `tests/md/test_reconnect.py` | Tests for deleted WsSession reconnect |

---

## Task 0: Proto vendor + codegen + connectivity spike

**Files:**
- Create: `trader/proto/` (full tree of vendored `.proto` files)
- Create: `scripts/regen_proto.sh`
- Create: `scripts/fix_grpc_imports.py`
- Create: `scripts/spike_last_quote.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Update pyproject.toml**

Replace the `[tool.poetry.dependencies]` block with:

```toml
[tool.poetry.dependencies]
python = "^3.12"
httpx = {extras = ["http2"], version = "^0.27"}
pydantic-settings = "^2.3"
structlog = "^24.4"
grpcio = ">=1.63,<2"
protobuf = ">=5.26,<6"
googleapis-common-protos = ">=1.63"

[tool.poetry.group.dev.dependencies]
pytest = "^8.2"
pytest-asyncio = "^0.23"
respx = "^0.21"
ruff = "^0.4"
pytest-mock = "^3.14"
hypothesis = "^6"
pytest-timeout = "^2"
grpcio-tools = ">=1.63,<2"
```

- [ ] **Step 2: Install deps**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
poetry install
```

Expected: resolves grpcio, protobuf, grpcio-tools, googleapis-common-protos. No errors.

- [ ] **Step 3: Vendor proto files**

```bash
PROTO_SRC=/tmp/finam-grpc/proto
PROTO_DST=~/workspaces/Shectory\ Trade\ \&\ Lab/trader/proto

mkdir -p "$PROTO_DST"
cp -r "$PROTO_SRC/grpc" "$PROTO_DST/"
cp -r "$PROTO_SRC/google/api" "$PROTO_DST/google/"
mkdir -p "$PROTO_DST/google"
cp -r "$PROTO_SRC/google/api" "$PROTO_DST/google/api"
cp -r "$PROTO_SRC/google/type" "$PROTO_DST/google/type"
```

Verify:
```bash
ls trader/proto/grpc/tradeapi/v1/marketdata/
# → marketdata_service.proto  marketdata_service.pb.go  ...
ls trader/proto/google/type/decimal.proto
# → trader/proto/google/type/decimal.proto
```

- [ ] **Step 4: Write scripts/fix_grpc_imports.py**

Create `scripts/fix_grpc_imports.py`:

```python
#!/usr/bin/env python3
"""
Fix conflicting absolute imports in grpcio-generated _pb2_grpc.py files.

grpcio-tools emits: import grpc.tradeapi.v1.marketdata.X_pb2 as alias
which conflicts with: import grpc  (the grpcio package)

Replacement:  from . import X_pb2 as alias
(safe because _pb2_grpc.py only ever imports _pb2 from its own directory)
"""
import re
import sys
from pathlib import Path


def fix_file(path: Path) -> bool:
    text = path.read_text()
    new_text = re.sub(
        r"^import (grpc\.tradeapi\.\S+) as (\S+)",
        lambda m: f"from . import {m.group(1).split('.')[-1]} as {m.group(2)}",
        text,
        flags=re.MULTILINE,
    )
    if new_text != text:
        path.write_text(new_text)
        return True
    return False


if __name__ == "__main__":
    gen_root = Path(sys.argv[1])
    fixed = [p for p in gen_root.rglob("*_pb2_grpc.py") if fix_file(p)]
    for p in fixed:
        print(f"Fixed: {p}")
    print(f"Done — {len(fixed)} file(s) patched.")
```

- [ ] **Step 5: Write scripts/regen_proto.sh**

Create `scripts/regen_proto.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_ROOT="$REPO_ROOT/trader/proto"
GEN_ROOT="$REPO_ROOT/trader/proto/gen"

# Bundled google/protobuf protos shipped with grpcio-tools
GRPC_TOOLS_PROTO="$(python -c "
import grpc_tools, os
print(os.path.join(os.path.dirname(grpc_tools.__file__), '_proto'))
")"

rm -rf "$GEN_ROOT"
mkdir -p "$GEN_ROOT"

python -m grpc_tools.protoc \
  -I "$PROTO_ROOT" \
  -I "$GRPC_TOOLS_PROTO" \
  --python_out="$GEN_ROOT" \
  --grpc_python_out="$GEN_ROOT" \
  grpc/tradeapi/v1/marketdata/marketdata_service.proto \
  grpc/tradeapi/v1/side.proto \
  grpc/gateway/protoc_gen_openapiv2/options/annotations.proto \
  google/api/annotations.proto \
  google/api/http.proto \
  google/type/decimal.proto \
  google/type/interval.proto

# Add __init__.py to every generated package directory
find "$GEN_ROOT" -type d -exec touch {}/__init__.py \;

# Fix conflicting grpc.* imports in _pb2_grpc.py files
python "$REPO_ROOT/scripts/fix_grpc_imports.py" "$GEN_ROOT"

echo "Regen complete. Generated files in $GEN_ROOT"
```

```bash
chmod +x scripts/regen_proto.sh
```

- [ ] **Step 6: Run codegen and verify**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
poetry run bash scripts/regen_proto.sh
```

Expected output ends with: `Regen complete.` and `N file(s) patched.`

Verify stubs exist:
```bash
ls trader/proto/gen/grpc/tradeapi/v1/marketdata/
# → __init__.py  marketdata_service_pb2.py  marketdata_service_pb2_grpc.py
python -c "
import sys; sys.path.insert(0, 'trader/proto/gen')
from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md
print(md.QuoteRequest(symbol='SBER@MISX'))
"
```

Expected: prints `symbol: "SBER@MISX"` — proto is importable.

- [ ] **Step 7: Update .gitignore**

Add to `.gitignore` in the project root:

```
trader/proto/gen/
```

- [ ] **Step 8: Write spike script**

Create `scripts/spike_last_quote.py`:

```python
#!/usr/bin/env python3
"""
Spike: call LastQuote against live Finam API to validate gRPC connectivity.
Usage:
  set -a && source ~/.shectory_trade.env && set +a
  poetry run python scripts/spike_last_quote.py
"""
import asyncio
import os
import sys

sys.path.insert(0, "trader/proto/gen")

import httpx
import grpc
import grpc.aio
from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
from grpc.tradeapi.v1.marketdata import marketdata_service_pb2_grpc as md_grpc

GRPC_TARGET = "api.finam.ru:443"
REST_BASE = "https://api.finam.ru"
SYMBOL = os.getenv("FINAM_MVP_SYMBOL", "SBER@MISX")


async def get_token(secret: str) -> str:
    async with httpx.AsyncClient(http2=True, base_url=REST_BASE) as http:
        r = await http.post("/v1/sessions", json={"secret": secret})
        r.raise_for_status()
        return r.json()["token"]


async def main() -> None:
    secret = os.environ["FINAM_SECRET_TOKEN"]
    print(f"Fetching token from {REST_BASE}...")
    token = await get_token(secret)
    print(f"Token obtained (length={len(token)})")

    creds = grpc.ssl_channel_credentials()
    channel_options = [
        ("grpc.keepalive_time_ms", 20_000),
        ("grpc.keepalive_timeout_ms", 10_000),
    ]

    print(f"Connecting to {GRPC_TARGET} ...")
    async with grpc.aio.secure_channel(GRPC_TARGET, creds, options=channel_options) as channel:
        stub = md_grpc.MarketDataServiceStub(channel)
        req = md_pb2.QuoteRequest(symbol=SYMBOL)
        metadata = [("authorization", token)]
        print(f"Calling LastQuote(symbol={SYMBOL!r})...")
        resp = await stub.LastQuote(req, metadata=metadata)
        q = resp.quote
        print(f"  symbol={resp.symbol}")
        print(f"  bid={q.bid.value}  ask={q.ask.value}  last={q.last.value}")
        print(f"  timestamp={q.timestamp}")
    print("Spike PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 9: Run spike**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
set -a && source ~/.shectory_trade.env && set +a
poetry run python scripts/spike_last_quote.py
```

Expected: prints bid/ask/last values and `Spike PASSED.`

If gRPC error: note the status code (e.g. `UNAVAILABLE`, `UNAUTHENTICATED`) — it reveals whether TLS or auth is the issue.

- [ ] **Step 10: Commit**

```bash
rtk git add pyproject.toml trader/proto/ scripts/ .gitignore
rtk git commit -m "feat(M1): vendor Finam proto, codegen scripts, spike — gRPC connectivity confirmed"
```

---

## Task 1: quote_from_proto + unit tests

**Files:**
- Create: `trader/md/grpc_client.py` (quote_from_proto only, no QuoteStream yet)
- Create: `tests/md/test_grpc_client.py` (mapping tests only)

- [ ] **Step 1: Write failing tests for quote_from_proto**

Create `tests/md/test_grpc_client.py`:

```python
# tests/md/test_grpc_client.py
"""Unit tests for grpc_client — quote_from_proto and QuoteStream."""
import sys
sys.path.insert(0, "trader/proto/gen")

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.md.grpc_client import quote_from_proto
from trader.md.models import Quote


def _make_pb_quote(symbol="GZM6@RTSX", bid="100.5", ask="100.6",
                   last="100.55", bid_size="10", ask_size="5", last_size="3",
                   ts_sec=1_716_000_000):
    """Build a real proto Quote object."""
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    from google.protobuf import timestamp_pb2

    ts = timestamp_pb2.Timestamp()
    ts.seconds = ts_sec
    ts.nanos = 0

    def _dec(v):
        from google.type import decimal_pb2
        d = decimal_pb2.Decimal()
        d.value = v
        return d

    q = md_pb2.Quote()
    q.symbol = symbol
    q.timestamp.CopyFrom(ts)
    q.bid.CopyFrom(_dec(bid))
    q.ask.CopyFrom(_dec(ask))
    q.last.CopyFrom(_dec(last))
    q.bid_size.CopyFrom(_dec(bid_size))
    q.ask_size.CopyFrom(_dec(ask_size))
    q.last_size.CopyFrom(_dec(last_size))
    return q


def test_quote_from_proto_maps_symbol():
    pb = _make_pb_quote(symbol="SBER@MISX")
    raw = quote_from_proto(pb)
    assert raw["symbol"] == "SBER@MISX"


def test_quote_from_proto_maps_decimal_fields():
    pb = _make_pb_quote(bid="123.45", ask="123.50", last="123.47")
    raw = quote_from_proto(pb)
    assert raw["bid"] == "123.45"
    assert raw["ask"] == "123.50"
    assert raw["last"] == "123.47"


def test_quote_from_proto_maps_size_fields_to_int():
    pb = _make_pb_quote(bid_size="50", ask_size="20", last_size="7")
    raw = quote_from_proto(pb)
    assert raw["bid_size"] == 50
    assert raw["ask_size"] == 20
    assert raw["last_size"] == 7


def test_quote_from_proto_timestamp_is_utc_iso():
    pb = _make_pb_quote(ts_sec=1_716_000_000)
    raw = quote_from_proto(pb)
    # Must be parseable by Quote.from_payload
    dt = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
    assert dt.tzinfo is not None
    assert dt == datetime.fromtimestamp(1_716_000_000, tz=timezone.utc)


def test_quote_from_proto_empty_decimal_becomes_zero():
    """Empty decimal (no bid on market) maps to '0', not crash."""
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    from google.protobuf import timestamp_pb2
    pb = md_pb2.Quote()
    pb.symbol = "X"
    pb.timestamp.CopyFrom(timestamp_pb2.Timestamp(seconds=1_716_000_000))
    # bid/ask/last left unset → empty Decimal with value=""
    raw = quote_from_proto(pb)
    assert raw["bid"] == "0"
    assert raw["bid_size"] == 0


def test_quote_from_proto_is_parseable_by_from_payload():
    """End-to-end: proto → dict → Quote dataclass."""
    pb = _make_pb_quote()
    raw = quote_from_proto(pb)
    quote = Quote.from_payload(raw["symbol"], raw)
    assert quote.bid == Decimal("100.5")
    assert quote.ask == Decimal("100.6")
    assert quote.last == Decimal("100.55")
    assert quote.bid_size == 10
    assert quote.ask_size == 5
    assert quote.last_size == 3
    assert quote.timestamp.tzinfo is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab
poetry run pytest tests/md/test_grpc_client.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'quote_from_proto' from 'trader.md.grpc_client'`

- [ ] **Step 3: Implement quote_from_proto**

Create `trader/md/grpc_client.py`:

```python
import asyncio
import random
import sys
from collections.abc import AsyncIterator, Callable, Awaitable
from datetime import timezone

import grpc
import grpc.aio
import structlog

sys.path.insert(0, "trader/proto/gen")

log = structlog.get_logger()

GRPC_TARGET = "api.finam.ru:443"
RECONNECT_BASE = 0.1
RECONNECT_MAX = 60.0

CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]

_SENTINEL = object()


def _backoff(attempt: int) -> float:
    return random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))


def quote_from_proto(pb) -> dict:
    """Convert a proto Quote to the dict format expected by Quote.from_payload()."""
    ts = pb.timestamp.ToDatetime(tzinfo=timezone.utc)
    return {
        "symbol": pb.symbol,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "bid": pb.bid.value or "0",
        "bid_size": int(float(pb.bid_size.value or "0")),
        "ask": pb.ask.value or "0",
        "ask_size": int(float(pb.ask_size.value or "0")),
        "last": pb.last.value or "0",
        "last_size": int(float(pb.last_size.value or "0")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/md/test_grpc_client.py -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
rtk git add trader/md/grpc_client.py tests/md/test_grpc_client.py
rtk git commit -m "feat(M1): quote_from_proto — proto Quote → dict mapping, 6 tests green"
```

---

## Task 2: force_refresh in AsyncAuthClient

**Files:**
- Modify: `trader/auth/client.py:17` (`get_token` signature)
- Modify: `tests/auth/test_auth_client.py`

- [ ] **Step 1: Write failing test for force_refresh**

Open `tests/auth/test_auth_client.py` and add at the bottom:

```python
async def test_get_token_force_refresh_bypasses_cache(respx_mock):
    """force_refresh=True must re-fetch even if token is not expired."""
    from trader.auth.client import AsyncAuthClient
    respx_mock.post("https://api.finam.ru/v1/sessions").mock(
        return_value=httpx.Response(200, json={"token": "fresh_tok"})
    )
    respx_mock.post("https://api.finam.ru/v1/sessions/details").mock(
        return_value=httpx.Response(
            200,
            json={"expires_at": "2099-01-01T00:00:00Z"},
        )
    )
    client = AsyncAuthClient(
        base_url="https://api.finam.ru",
        secret_token="sec",
    )
    # Prime the cache with a non-expired token
    await client.get_token()
    # Second call: force_refresh must bypass the cache and re-fetch
    respx_mock.post("https://api.finam.ru/v1/sessions").mock(
        return_value=httpx.Response(200, json={"token": "second_tok"})
    )
    respx_mock.post("https://api.finam.ru/v1/sessions/details").mock(
        return_value=httpx.Response(200, json={"expires_at": "2099-01-01T00:00:00Z"})
    )
    tok = await client.get_token(force_refresh=True)
    assert tok == "second_tok"
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/auth/test_auth_client.py::test_get_token_force_refresh_bypasses_cache -v
```

Expected: `TypeError: AsyncAuthClient.get_token() got an unexpected keyword argument 'force_refresh'`

- [ ] **Step 3: Update get_token signature in trader/auth/client.py**

In `trader/auth/client.py`, change:

```python
    async def get_token(self) -> str:
        if self._cached_token and not self._cached_token.is_expired(self._refresh_before_secs):
            return self._cached_token.access_token
        self._cached_token = await self._fetch_token()
        return self._cached_token.access_token
```

to:

```python
    async def get_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._cached_token
            and not self._cached_token.is_expired(self._refresh_before_secs)
        ):
            return self._cached_token.access_token
        self._cached_token = await self._fetch_token()
        return self._cached_token.access_token
```

- [ ] **Step 4: Run all auth tests**

```bash
poetry run pytest tests/auth/ -v
```

Expected: all tests pass (previously green + the new force_refresh test).

- [ ] **Step 5: Commit**

```bash
rtk git add trader/auth/client.py tests/auth/test_auth_client.py
rtk git commit -m "feat(auth): add force_refresh=False to get_token for expired-token recovery"
```

---

## Task 3: QuoteStream class + unit tests

**Files:**
- Modify: `trader/md/grpc_client.py` (add `QuoteStream` class)
- Modify: `tests/md/test_grpc_client.py` (add `QuoteStream` tests)

- [ ] **Step 1: Write failing tests for QuoteStream**

Append to `tests/md/test_grpc_client.py`:

```python
# --- QuoteStream unit tests ---

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_subscribe_response(quotes_data: list[dict]):
    """Build a SubscribeQuoteResponse proto with Quote list."""
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    resp = md_pb2.SubscribeQuoteResponse()
    for d in quotes_data:
        pb_q = _make_pb_quote(**d)
        resp.quote.append(pb_q)
    return resp


def _make_error_response(code: int, description: str):
    from grpc.tradeapi.v1.marketdata import marketdata_service_pb2 as md_pb2
    resp = md_pb2.SubscribeQuoteResponse()
    resp.error.code = code
    resp.error.description = description
    return resp


def _make_stub(responses: list, *, raise_after: Exception | None = None):
    """Return a fake stub whose SubscribeQuote is an async generator."""
    stub = MagicMock()

    async def _gen(*args, **kwargs):
        for r in responses:
            yield r
        if raise_after:
            raise raise_after
        # else: clean exit (simulates server closing stream)

    stub.SubscribeQuote = MagicMock(return_value=_gen())
    return stub


async def test_subscribe_starts_task_and_quotes_appear_in_iter_quotes():
    from trader.md.grpc_client import QuoteStream

    resp = _make_subscribe_response([{}])  # one quote with defaults

    qs = QuoteStream()
    await qs.start(get_token=AsyncMock(return_value="tok"))

    stub = _make_stub([resp])

    with (
        patch("trader.md.grpc_client.grpc.ssl_channel_credentials"),
        patch("trader.md.grpc_client.grpc.aio.secure_channel"),
        patch("trader.md.grpc_client.MarketDataServiceStub", return_value=stub),
    ):
        await qs.subscribe("GZM6@RTSX")

        received = []
        async for raw in qs.iter_quotes():
            received.append(raw)
            break

    await qs.close()
    assert len(received) == 1
    assert received[0]["symbol"] == "GZM6@RTSX"


async def test_stream_error_in_payload_is_skipped():
    """StreamError in response body is logged and skipped — does not raise."""
    from trader.md.grpc_client import QuoteStream

    err_resp = _make_error_response(code=503, description="service unavailable")
    quote_resp = _make_subscribe_response([{}])

    qs = QuoteStream()
    await qs.start(get_token=AsyncMock(return_value="tok"))

    stub = _make_stub([err_resp, quote_resp])

    with (
        patch("trader.md.grpc_client.grpc.ssl_channel_credentials"),
        patch("trader.md.grpc_client.grpc.aio.secure_channel"),
        patch("trader.md.grpc_client.MarketDataServiceStub", return_value=stub),
    ):
        await qs.subscribe("GZM6@RTSX")
        received = []
        async for raw in qs.iter_quotes():
            received.append(raw)
            break

    await qs.close()
    # Must have received the valid quote (error response was skipped)
    assert len(received) == 1


async def test_backoff_cap_never_exceeds_reconnect_max():
    from trader.md.grpc_client import RECONNECT_BASE, RECONNECT_MAX, _backoff

    for attempt in range(100):
        delay = _backoff(attempt)
        assert delay <= RECONNECT_MAX, f"attempt {attempt}: delay {delay} > {RECONNECT_MAX}"


async def test_close_drains_sentinel_so_iter_quotes_exits():
    """close() must unblock an in-progress iter_quotes() call."""
    from trader.md.grpc_client import QuoteStream

    qs = QuoteStream()
    await qs.start(get_token=AsyncMock(return_value="tok"))

    # No subscribe — just test that close() sends sentinel
    exited = asyncio.Event()

    async def _consume():
        async for _ in qs.iter_quotes():
            pass
        exited.set()

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.01)
    await qs.close()
    await asyncio.wait_for(exited.wait(), timeout=2.0)
    task.cancel()
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
poetry run pytest tests/md/test_grpc_client.py -k "QuoteStream or backoff or close_drains" -v 2>&1 | tail -10
```

Expected: `ImportError` or `AttributeError` — QuoteStream not yet defined.

- [ ] **Step 3: Implement QuoteStream in trader/md/grpc_client.py**

Replace the full content of `trader/md/grpc_client.py` with:

```python
import asyncio
import random
import sys
from collections.abc import AsyncIterator, Callable, Awaitable
from datetime import timezone

import grpc
import grpc.aio
import structlog

sys.path.insert(0, "trader/proto/gen")

from grpc.tradeapi.v1.marketdata.marketdata_service_pb2_grpc import MarketDataServiceStub
from grpc.tradeapi.v1.marketdata.marketdata_service_pb2 import SubscribeQuoteRequest

log = structlog.get_logger()

GRPC_TARGET = "api.finam.ru:443"
RECONNECT_BASE = 0.1
RECONNECT_MAX = 60.0

CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]

_SENTINEL = object()


def _backoff(attempt: int) -> float:
    return random.uniform(0, min(RECONNECT_BASE * (2 ** attempt), RECONNECT_MAX))


def quote_from_proto(pb) -> dict:
    """Convert a proto Quote to the dict format expected by Quote.from_payload()."""
    ts = pb.timestamp.ToDatetime(tzinfo=timezone.utc)
    return {
        "symbol": pb.symbol,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "bid": pb.bid.value or "0",
        "bid_size": int(float(pb.bid_size.value or "0")),
        "ask": pb.ask.value or "0",
        "ask_size": int(float(pb.ask_size.value or "0")),
        "last": pb.last.value or "0",
        "last_size": int(float(pb.last_size.value or "0")),
    }


class QuoteStream:
    """gRPC streaming client for Finam market data. Same interface as WsSession."""

    def __init__(self) -> None:
        self._channel: grpc.aio.Channel | None = None
        self._get_token: Callable[[bool], Awaitable[str]] | None = None
        self._data_q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self.messages_received: int = 0

    async def start(self, get_token: Callable[[bool], Awaitable[str]]) -> None:
        self._get_token = get_token
        self._running = True
        creds = grpc.ssl_channel_credentials()
        self._channel = grpc.aio.secure_channel(GRPC_TARGET, creds, options=CHANNEL_OPTIONS)

    async def subscribe(self, symbol: str) -> None:
        if symbol in self._stream_tasks and not self._stream_tasks[symbol].done():
            return
        task = asyncio.create_task(self._stream_symbol(symbol))
        self._stream_tasks[symbol] = task

    async def _stream_symbol(self, symbol: str) -> None:
        attempt = 0
        while self._running:
            try:
                force = attempt > 0  # force-refresh token after first failure
                token = await self._get_token(force)
                metadata = [("authorization", token)]
                stub = MarketDataServiceStub(self._channel)
                req = SubscribeQuoteRequest(symbols=[symbol])
                async for resp in stub.SubscribeQuote(req, metadata=metadata):
                    if resp.error.code:
                        log.warning(
                            "md.stream_error",
                            symbol=symbol,
                            code=resp.error.code,
                            description=resp.error.description,
                        )
                        continue
                    for pb_quote in resp.quote:
                        self._put_data(quote_from_proto(pb_quote))
                        self.messages_received += 1
                attempt = 0  # clean stream exit — reconnect immediately, no delay
            except grpc.aio.AioRpcError as exc:
                if not self._running:
                    return
                log.warning(
                    "md.rpc_error",
                    symbol=symbol,
                    code=str(exc.code()),
                    attempt=attempt,
                )
                delay = _backoff(attempt)
                await asyncio.sleep(delay)
                attempt += 1
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if not self._running:
                    return
                log.error("md.stream_crashed", symbol=symbol, exc=str(exc))
                delay = _backoff(attempt)
                await asyncio.sleep(delay)
                attempt += 1

    def _put_data(self, msg: dict) -> None:
        if self._data_q.full():
            try:
                self._data_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            log.warning("md.queue_overflow")
        self._data_q.put_nowait(msg)

    async def iter_quotes(self) -> AsyncIterator[dict]:
        while True:
            item = await self._data_q.get()
            if item is _SENTINEL:
                self._data_q.put_nowait(_SENTINEL)  # re-enqueue for any other consumers
                return
            yield item

    async def close(self, code: int = 1000) -> None:
        self._running = False
        for task in self._stream_tasks.values():
            task.cancel()
        if self._stream_tasks:
            await asyncio.gather(*self._stream_tasks.values(), return_exceptions=True)
        self._stream_tasks.clear()
        if self._channel:
            await self._channel.close()
        self._data_q.put_nowait(_SENTINEL)
```

- [ ] **Step 4: Run all grpc_client tests**

```bash
poetry run pytest tests/md/test_grpc_client.py -v
```

Expected: `10 passed` (6 mapping + 4 QuoteStream)

- [ ] **Step 5: Run full unit suite to verify no regressions**

```bash
poetry run pytest -m "not integration" -v 2>&1 | tail -5
```

Expected: same count as before (49+) passed, 0 failures.

- [ ] **Step 6: Commit**

```bash
rtk git add trader/md/grpc_client.py tests/md/test_grpc_client.py
rtk git commit -m "feat(M1): QuoteStream — gRPC fan-in streaming client, 10 tests green"
```

---

## Task 4: feed.py adapter + test_feed.py update

**Files:**
- Modify: `trader/md/feed.py` (swap WsSession → QuoteStream injection)
- Modify: `tests/md/test_feed.py` (replace make_mock_ws → make_mock_qs)

- [ ] **Step 1: Run feed tests to confirm they currently pass (baseline)**

```bash
poetry run pytest tests/md/test_feed.py -v 2>&1 | tail -5
```

Expected: all 11 tests pass.

- [ ] **Step 2: Update feed.py**

In `trader/md/feed.py`, make these changes:

**Change imports** — remove any WsSession import if present (there isn't one, feed.py is injected).

**Change `__init__`** — rename parameter and remove `on_reconnect`:

```python
# Old:
class MarketDataFeed:
    def __init__(
        self,
        ws: WsSession,
        watchdog_secs: float = 5.0,
        on_raw: Callable[[dict], None] | None = None,
    ) -> None:
        ...
        self._ws = ws

# New:
class MarketDataFeed:
    def __init__(
        self,
        qs,  # QuoteStream — typed loosely to avoid circular import
        watchdog_secs: float = 5.0,
        on_raw: Callable[[dict], None] | None = None,
    ) -> None:
        ...
        self._qs = qs
```

**Change `start()`**:

```python
# Old:
    async def start(self, get_token: Callable[[], Awaitable[str]]) -> None:
        self._running = True
        await self._ws.connect(
            get_token=get_token,
            on_reconnect=self._resubscribe_all,
        )
        self._reader_task = asyncio.create_task(self._reader())
        self._watchdog_task = asyncio.create_task(self._watchdog())

# New:
    async def start(self, get_token: Callable[[], Awaitable[str]]) -> None:
        self._running = True
        await self._qs.start(get_token=get_token)
        self._reader_task = asyncio.create_task(self._reader())
        self._watchdog_task = asyncio.create_task(self._watchdog())
```

**Change `add_symbol()`** — replace `self._ws.subscribe` → `self._qs.subscribe`:

```python
    async def add_symbol(self, symbol: str) -> None:
        if symbol not in self._slots:
            self._slots[symbol] = QuoteState()
        if symbol not in self._active_symbols:
            self._active_symbols.add(symbol)
            await self._qs.subscribe(symbol)
```

**Change `_reader()`** — replace `self._ws.iter_quotes()` → `self._qs.iter_quotes()`:

```python
    async def _reader(self) -> None:
        try:
            async for raw in self._qs.iter_quotes():
                ...
```

**Change `aclose()`** — replace `self._ws.close(code=1000)` → `await self._qs.close()`:

```python
    async def aclose(self) -> None:
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
        await self._qs.close()
        if self._reader_task:
            try:
                await asyncio.wait_for(self._reader_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader_task.cancel()
        self._state = FeedState.CLOSED
        for slot in self._slots.values():
            slot._closed = True
            slot._event.set()
```

**Delete `_resubscribe_all()`** — no longer needed (QuoteStream handles reconnect internally).

- [ ] **Step 3: Update tests/md/test_feed.py**

Replace the imports and `make_mock_ws` helper at the top of `tests/md/test_feed.py`:

```python
# Old imports:
from trader.md.ws_client import WsSession

# New imports: (remove WsSession, nothing to import from grpc_client for unit tests)
```

Replace `make_mock_ws` helper:

```python
# Old:
def make_mock_ws(quote_frames: list[dict] | None = None) -> WsSession:
    ws = AsyncMock(spec=WsSession)
    ws.connected = True
    ws.connect = AsyncMock()
    ws.subscribe = AsyncMock()
    ws.close = AsyncMock()
    frames = quote_frames or []
    async def fake_iter_quotes():
        for f in frames:
            yield f
        await asyncio.sleep(9999)
    ws.iter_quotes = fake_iter_quotes
    return ws

# New:
def make_mock_qs(quote_frames: list[dict] | None = None):
    qs = AsyncMock()
    qs.start = AsyncMock()
    qs.subscribe = AsyncMock()
    qs.close = AsyncMock()
    frames = quote_frames or []
    async def fake_iter_quotes():
        for f in frames:
            yield f
        await asyncio.sleep(9999)
    qs.iter_quotes = fake_iter_quotes
    return qs
```

Replace every occurrence of `make_mock_ws(` → `make_mock_qs(` and `MarketDataFeed(ws,` → `MarketDataFeed(qs,` throughout the test file.

Also replace `feed.start(get_token=AsyncMock(return_value="tok"))` calls — the signature is unchanged.

- [ ] **Step 4: Run feed tests**

```bash
poetry run pytest tests/md/test_feed.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Run full unit suite**

```bash
poetry run pytest -m "not integration" -v 2>&1 | tail -5
```

Expected: all tests pass, no regressions.

- [ ] **Step 6: Commit**

```bash
rtk git add trader/md/feed.py tests/md/test_feed.py
rtk git commit -m "feat(M1): wire feed.py to QuoteStream, update feed unit tests"
```

---

## Task 5: Integration test update

**Files:**
- Modify: `tests/md/test_integration.py`

- [ ] **Step 1: Update test_integration.py**

Replace the full content of `tests/md/test_integration.py`:

```python
"""
Integration tests — require real Finam credentials + live market hours.

Setup:
  set -a && source ~/.shectory_trade.env && set +a

Run:
  poetry run pytest tests/md/test_integration.py -v -m integration
"""
import asyncio

import pytest

from trader.config import Settings
from trader.auth.client import AsyncAuthClient
from trader.md.feed import MarketDataFeed
from trader.md.models import FeedState
from trader.md.grpc_client import QuoteStream

pytestmark = pytest.mark.integration

SYMBOL = "GZM6@RTSX"


async def _wait_for_state(feed: MarketDataFeed, target: FeedState, timeout: float = 30.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while feed.state != target:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Feed did not reach {target} within {timeout}s")
        await asyncio.sleep(0.1)


@pytest.fixture
async def feed():
    settings = Settings()
    auth = AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    )
    qs = QuoteStream()
    _feed = MarketDataFeed(qs, watchdog_secs=5.0)
    await _feed.start(get_token=auth.get_token)
    yield _feed
    await _feed.aclose()
    await auth.aclose()


@pytest.mark.timeout(60)
async def test_feed_state_is_live_after_first_quote(feed):
    await feed.add_symbol(SYMBOL)
    await _wait_for_state(feed, FeedState.LIVE, timeout=30.0)
    assert feed.state == FeedState.LIVE


@pytest.mark.timeout(60)
async def test_live_quote_received(feed):
    await feed.add_symbol(SYMBOL)
    async for quote in feed.subscribe(SYMBOL):
        assert quote.bid >= 0
        assert quote.ask >= 0
        assert quote.timestamp.tzinfo is not None
        assert feed.state == FeedState.LIVE
        break


@pytest.mark.timeout(60)
async def test_second_subscribe_reuses_slot(feed):
    await feed.add_symbol(SYMBOL)
    await _wait_for_state(feed, FeedState.LIVE, timeout=30.0)
    async for quote in feed.subscribe(SYMBOL):
        assert quote is not None
        break
```

- [ ] **Step 2: Run integration tests (if market is open)**

```bash
set -a && source ~/.shectory_trade.env && set +a
poetry run pytest tests/md/test_integration.py -v -m integration --timeout=60
```

Expected: 3 passed. If market is closed: `FeedState.LIVE` timeout after 30s — expected; skip and note in commit message.

- [ ] **Step 3: Commit**

```bash
rtk git add tests/md/test_integration.py
rtk git commit -m "feat(M1): update integration tests to use QuoteStream"
```

---

## Task 6: Cleanup — delete WebSocket code

**Files:**
- Delete: `trader/md/ws_client.py`
- Delete: `tests/md/test_ws_client.py`
- Delete: `tests/md/test_reconnect.py`
- Modify: `pyproject.toml` (remove websockets, orjson)
- Modify: `README.md`

- [ ] **Step 1: Verify nothing imports ws_client**

```bash
grep -r "ws_client\|WsSession\|from trader.md.ws_client" trader/ tests/ --include="*.py"
```

Expected: zero results (all references removed in Task 4).

- [ ] **Step 2: Delete WebSocket files**

```bash
rm trader/md/ws_client.py tests/md/test_ws_client.py tests/md/test_reconnect.py
```

- [ ] **Step 3: Remove websockets and orjson from pyproject.toml**

In `pyproject.toml`, remove the lines:

```toml
websockets = "^13.1"
orjson = "^3.10"
```

Then run:

```bash
poetry install
```

Expected: resolves without websockets/orjson. No import errors.

- [ ] **Step 4: Run full unit suite**

```bash
poetry run pytest -m "not integration" -v 2>&1 | tail -10
```

Expected: all tests pass, no `ws_client` import errors.

- [ ] **Step 5: Update README.md**

Replace the M1 row in the module table:

```markdown
| M1 — Market Data (`trader/md/`) | ✅ gRPC rewrite — WsSession replaced by QuoteStream, unit tests green |
```

Update the unit suite count to reflect current count from the test run above.

- [ ] **Step 6: Final commit**

```bash
rtk git add -u && rtk git add README.md
rtk git commit -m "feat(M1): remove WebSocket stub, M1 gRPC rewrite complete"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|-----------------|-----------------|
| Proto vendoring (full closure) | Task 0 step 3 |
| regen_proto.sh + import fixer | Task 0 steps 4-5 |
| Spike: LastQuote validation | Task 0 steps 8-9 |
| `grpcio>=1.63` pinning | Task 0 step 1 |
| `quote_from_proto` + Decimal mapping | Task 1 |
| `force_refresh` in AsyncAuthClient | Task 2 |
| `QuoteStream` fan-in (one stream per symbol) | Task 3 |
| `StreamError` payload handling (log + skip) | Task 3 (test_stream_error_in_payload_is_skipped) |
| Keepalive channel options | Task 3 (CHANNEL_OPTIONS in grpc_client.py) |
| Backoff cap test | Task 3 (test_backoff_cap) |
| `feed.py` adapter (swap WsSession → QuoteStream) | Task 4 |
| Integration test (live quote) | Task 5 |
| Delete ws_client.py + websockets dep | Task 6 |
| `.gitignore` for gen/ | Task 0 step 7 |
| Real proto objects in unit tests (not MagicMock) | Task 1 (`_make_pb_quote` uses real protos) |

All spec requirements are covered. No gaps found.
