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

## Approach
- Read existing files before writing. Don't re-read unless changed.
- Thorough in reasoning, concise in output.
- Skip files over 100KB unless required.
- No sycophantic openers or closing fluff.
- No emojis or em-dashes.
- Do not guess APIs, versions, flags, commit SHAs, or package names. Verify by reading code or docs before asserting.

# Core Rules

Short sentences only (8-10 words max).
No filler, no preamble, no pleasantries.
Tool first. Result first. No explain unless asked.
Code stays normal. English gets compressed.

---

## Formatting

Output sounds human. Never AI-generated.
Never use em-dashes or replacement hyphens.
Avoid parenthetical clauses entirely.
Hyphens map to standard grammar only.

---

## Usage

Paste at session start or drop as CLAUDE.md in project root.