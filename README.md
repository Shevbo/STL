# Shectory Trader

Execution platform for FORTS ММВБ via Finam Trade API.

## Modules

| Module | Status |
|--------|--------|
| M0 — Auth (`trader/auth/`) | ✅ recovered, tests green |
| M10 — Config (`trader/config.py`) | ✅ recovered, tests green |
| M4 — Instrument Registry (`trader/registry/`) | ✅ recovered, tests green |
| M1 — Market Data (`trader/md/`) | ⚠️ recovered from plan (pre-review draft) — see notes |
| Frontend (`frontend/`) | ✅ Svelte 5 + Vite, see `frontend/` |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install 'httpx[http2]' pydantic-settings structlog websockets orjson \
    pytest pytest-asyncio respx pytest-mock hypothesis pytest-timeout ruff
```

## Tests

```bash
.venv/bin/python -m pytest -m "not integration"   # unit suite
.venv/bin/python -m pytest -m integration         # needs real Finam token
```

## Recovery note (2026-05-16)

The backend was lost in a hard reset (never committed to git) and reconstructed from
Claude Code `file-history` backups and the intact superpowers implementation plans
under `docs/superpowers/plans/`. M0/M10/M4 are byte-exact. M1 (Market Data) was
rebuilt from the implementation plan, which holds the pre-code-review draft — the
review-cycle fixes were not in any backup, so part of the M1 test suite still fails
and M1 needs its review pass re-run.
