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

# grpcio's `grpc` package is a regular (non-namespace) package, so we must
# explicitly extend its __path__ to include our generated stubs directory.
# This must happen BEFORE any grpc.tradeapi.* imports.
import grpc
import grpc.aio
_gen_grpc = os.path.join(os.path.dirname(__file__), "..", "trader", "proto", "gen", "grpc")
if _gen_grpc not in grpc.__path__:
    grpc.__path__.append(_gen_grpc)

import httpx
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
