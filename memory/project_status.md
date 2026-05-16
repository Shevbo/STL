---
name: project-status
description: "Current implementation status of Shectory Trader modules and what's next"
metadata: 
  node_type: memory
  type: project
  originSessionId: ca32cb53-c602-4a4e-9d55-91697f0df623
---

M1 Market Data (trader/md/) complete as of 2026-05-16. 13 commits pushed to origin/main.

**Why:** M1 is Stage 4 of the TZ; provides reconnecting WebSocket feed of real-time QUOTES from Finam Trade API with conflated-slot consumer interface.

**What was built:**
- `trader/md/models.py` — FeedState enum + Quote frozen dataclass
- `trader/md/ws_client.py` — WsSession (connect, auth, subscribe, iter_quotes, reconnect with full-jitter backoff, auth 401 handling)
- `trader/md/feed.py` — MarketDataFeed (QuoteState conflated slots, watchdog, aclose)
- 48 unit tests passing; 3 integration tests (require real Finam creds + live market hours)

**Module status (CLAUDE.md):**
- M0 Auth ✅, M10 Config ✅, M4 Instrument Registry ✅, M1 Market Data ✅
- M2 TX Adapter 🔲, M3 OMS Core 🔲, M5 Positions 🔲, M7 Audit Log 🔲, M8 Trader API 🔲, M6 Risk Gate 🔲

**How to apply:** Next session should start with M2 TX Adapter (Stage 5 TZ) — need brainstorming + design spec first. Integration tests for M1 require live credentials and market hours (not CI-safe).

**Key protocol TODOs in ws_client.py:**
- WS_URL (wss://api.finam.ru:443/ws) — verify from Finam docs
- Auth message format `{"type": "auth", "token": "..."}` — verify
- Subscribe ack format `{"type": "subscribe_ack", "symbol": "..."}` — verify
These must be validated against real Finam API before M1 integration tests can run.
