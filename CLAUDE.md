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
