# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Shectory Trade & Lab (STL) is a live-trading platform for FORTS (MOEX derivatives). Three
codebases in one repo, joined by one gRPC contract:

- **STL backend** (`trader/`) — Python 3.12 / FastAPI / asyncio. Executes orders via the
  Finam Trade API (HTTP/2) and via a local QUIK terminal (through the agent below). Also
  hosts the Lab strategy framework + paper-robot backtester.
- **QUIK agent** (`quik_agent/`) — Go single-exe, runs on a Windows VDS next to a QUIK
  terminal. Reads market data from QUIK over DDE, DIALS OUT to STL over one long-lived
  bidi gRPC stream (so the Windows box needs no inbound port), and places orders through a
  QLua `sendTransaction` bridge. Self-updates.
- **Frontend** (`frontend/`) — Svelte 5 SPA (lightweight-charts + uplot), built to static
  assets and served by nginx; `/api` + `/ws` are proxied to uvicorn.

Deploy target: a hoster (Ubuntu, ssh alias `hoster`, 83.69.248.175) running
`shectory-trader.service` (uvicorn :8000) + `shectory-optimizer.service`. App dir
`~/apps/shectory-trader`. Canonical remote `github.com/Shevbo/STL.git` (local remotes
`origin` and `github` both point there).

## Architecture (the parts that span files)

**The wire contract is the center of gravity.** `proto/shectory/quik/v1/quik_agent.proto`
defines the agent↔STL stream: agent→STL `AgentMessage` (Register/Heartbeat/ticks/order
book/OrderUpdate/TransReply/LimitsState), STL→agent `OrchestratorMessage` (Ack/PlaceOrder/
CancelOrder/ReplaceOrder/KillSwitch/StartExecution/SetLimits). Regenerate stubs after any
edit (see Codegen + the protobuf gotcha below).

**Order flow (human-initiated only):** UI → `POST /api/v1/quik/orders/*`
(`trader/api/quik_orders.py`) → hard-limit check (`trader/quik/limits.py`) → gRPC
`PlaceOrder` enqueued by `trader/quik/server.py` → agent `trade.Manager`
(`quik_agent/internal/trade/manager.go`) re-checks limits → QLua bridge (a file-queue
`C:\quik-bridge\cmd.jsonl`, because prod QUIK has no LuaSocket) → `shectory_trade.lua`
`sendTransaction` → QUIK → broker. Replies flow back as OrderUpdate/TransReply. STL keeps
order state in `trader/quik/orders.py` (`OrderStore`); read-only market state lives in
`trader/quik/store.py` (`QuikAgentStore`).

**Dual trading safety.** A QUIK order requires the master flag ON in BOTH STL
(`quik_trading_enabled` env) AND the agent's own `agent_config.json`. STL never pushes the
master flag; it pushes only the whitelist + numeric caps via `SetLimits` on connect (so the
two whitelists cannot silently diverge), and the agent echoes its effective limits back via
`LimitsState`. A rejected order text "Торговля QUIK отключена" therefore means the AGENT's
local flag is off. Phantom orders QUIK never acknowledges are reconciled to terminal on
BOTH sides (STL `OrderStore.reconcile_pending`, agent `Manager.reconcileStalePending`, ~20s)
so they cannot occupy the working-contracts budget forever.

**Broker abstraction** (`trader/broker/`): robots trade through one `BrokerInterface`
(`base.py`); the concrete adapter is chosen from `settings.exchange_interface` by
`registry.get_broker()`, which hard-gates live trading on `is_trade_ready()` (all CORE
capabilities present). `FinamBroker` and `QuikBroker` each declare their capabilities
honestly.

**Lab** (`trader/lab/`): paper robots persisted in Postgres table `robots` (the traded
symbol lives in `params_json`, NOT a column); `scheduler.py` runs them, `runtime.py` is the
per-robot execution context, `ai46/` is the flagship strategy. The backtester + optimizer
sweep jobs live here too (self-healing orphan reaper in `api/app.py`).

`trader/api/app.py` is the FastAPI app factory: its lifespan mounts routers, starts the
gRPC server, and launches background asyncio tasks (VDS fallback sweeper, orphan reaper,
QUIK order reconcile). It is large — grep it, don't read it whole.

## Commands

Single dev runner: `make` (bash/hoster/CI) or `dev.ps1` (Windows) — same verbs. Tools are
not auto-installed; `make check` reports the toolchain.

```bash
# Python (STL). Prod has no credentials-free integration deps; unit tests need none.
poetry install
poetry run pytest -m "not integration" -q          # or: make test-py
poetry run pytest tests/quik/test_store.py -q       # one file
poetry run pytest tests/quik/test_store.py::test_pick_prefers_single_green_when_others_stale -q  # one test
poetry run pytest -m integration -q                 # needs FINAM_SECRET_TOKEN
poetry run ruff check trader/ tests/                # lint (or: make lint)

# Go (QUIK agent). No Go toolchain locally — run these on the hoster (userland
# ~/go-sdk/go/bin, ~/go/bin, ~/protoc/bin already on PATH there).
cd quik_agent && go test ./...                      # or: make test-go
go test ./internal/trade/ -run TestReconcileStalePending
make gen                                            # regen Go + Python proto stubs
make build                                          # cross-build windows exe (amd64+386)

# Frontend (Svelte). Vite has trouble with the spaced path in Git Bash — build via node
# directly, or use PowerShell.
cd frontend && npm run build
npm test                                            # vitest run
```

## Deploy

- **STL backend/frontend:** `bash deploy/deploy.sh` (git push → ssh hoster → git pull +
  `npm run build` + `poetry install` + restart `shectory-trader.service`). A frontend-only
  change needs only `git pull && (cd frontend && npm run build)` on the hoster — nginx
  serves `frontend/dist`, so NO service restart (do not restart the backend while an
  operator is live-testing trading; a restart drops the agent gRPC link).
- **QUIK agent:** `bash deploy/publish_quik_agent.sh [agent_id]` ON THE HOSTER — it
  cross-builds the Windows exe from `~/quik_build` and publishes a release; the agent
  self-updates on start/03:00/command. Passing `agent_id` also fires the self-update
  trigger (`POST /api/v1/quik/agent/{id}/self-update`). `build_rev` must be a NUMERIC epoch.
- ssh aliases: `hoster` (prod), `smain` (federation/keymaster).

## Critical gotchas

- **Protobuf 5.29 (has bitten prod twice):** the prod protobuf runtime is 5.29.6.
  Regenerate the PYTHON stubs (`trader/quik/pb/...`) ONLY with `grpcio-tools<1.71` — its
  header reads "Protobuf Python Version: 5.29.0". grpcio-tools ≥1.81 emits gencode 6.x,
  which crash-loops STL on import in prod while passing locally (local runtime is 6.x).
  Verify that header before committing. On the hoster: a pinned venv (`grpcio-tools<1.71`,
  `protobuf==5.29.6`) then `python -m grpc_tools.protoc -I proto --python_out=trader/quik/pb
  --grpc_python_out=trader/quik/pb proto/shectory/quik/v1/quik_agent.proto`.
- **Config is env-only** (`trader/config.py`, pydantic-settings). Secrets come from
  keymaster (`ssh smain`); never hardcode or print secret VALUES (env name + path only).
- **Live trading is human-initiated.** Do not arm `quik_trading_enabled`, place orders, or
  deploy to prod without explicit operator permission. Real account = operator only.
- **Chart time is MSK.** Bar/trade timestamps are true UTC epochs; render Moscow time via
  `frontend/src/lib/chart-time.ts` (tickMarkFormatter/timeFormatter), never by shifting the
  data. Finam `/chart/bars` REST has M1/M5/M15/H1/H2/H4/D but NO M30, and returns empty for
  too-wide windows (the route shrinks + retries).

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
