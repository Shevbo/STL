# Shectory Trade & Lab — Project Instructions

## Overview
- **Shectory Trader**: execution platform for FORTS ММВБ via Finam Trade API (Python/FastAPI)
- **Shectory Lab**: strategy framework (future; connects to Trader via M8 Trader API)
- VDS: shectory-work (Ubuntu), deployed as systemd service

## Tech Stack
- Python 3.12, FastAPI, asyncio
- httpx[http2] — all HTTP calls (HTTP/2 required by Finam)
- pydantic-settings — config from env vars only
- structlog — structured logging
- pytest + pytest-asyncio — TDD, always test-first

## Commands
```bash
cd ~/workspaces/Shectory\ Trade\ \&\ Lab

poetry install                                      # install deps
poetry run pytest -m "not integration" -v           # unit tests (no credentials needed)
poetry run pytest -m integration -v                 # integration (needs FINAM_SECRET_TOKEN)
poetry run ruff check trader/ tests/                # lint
```

## Architecture Modules (from TZ)
| Module | Path | TZ Stage | Status |
|---|---|---|---|
| M0 Auth | trader/auth/ | Stage 2 | ✅ |
| M10 Config | trader/config.py | Stage 1 | ✅ |
| M4 Instrument Registry | trader/registry/ | Stage 3 | 🔲 |
| M1 Market Data | trader/md/ | Stage 4 | 🔲 |
| M2 TX Adapter | trader/tx/ | Stage 5 | 🔲 |
| M3 OMS Core | trader/oms/ | Stage 5 | 🔲 |
| M5 Positions | trader/positions/ | Stage 6 | 🔲 |
| M7 Audit Log | trader/audit/ | Stage 6 | 🔲 |
| M8 Trader API | trader/api/ | Stage 8 | 🔲 |
| M6 Risk Gate | trader/risk/ | Stage 9 | 🔲 |

## Key Constraints
- Secrets NEVER in git — use env vars or `~/.shectory_trade.env`
- All Finam calls: HTTP/2 via httpx
- Rate limit: 200 req/min per method
- Maintenance window: 05:00–06:15 МСК daily
- WebSocket: reconnect every 24h (auto-reconnect required in M1)
- TZ: docs/TZ_Shectory_Trader_v2_FINAM.md
