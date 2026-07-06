# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Shectory Trade & Lab (STL) is a live-trading platform for FORTS (MOEX derivatives). Four
codebases in one repo, joined by two gRPC contracts:

- **STL backend** (`trader/`) — Python 3.12 / FastAPI / asyncio. Control-plane + monitor +
  Finam Trade API execution (HTTP/2). Hosts the Lab strategy framework + backtester.
  NO heavy computation runs in this process (see isolation rule below).
- **QUIK agent** (`quik_agent/`) — Go single-exe on a Windows VDS next to a QUIK terminal.
  DIALS OUT to STL over one long-lived bidi gRPC stream (no inbound port on Windows).
  Market data comes from a QLua publisher over a file queue (DDE is retired, see below);
  orders go through the same QLua `sendTransaction` bridge. Self-updates; supervises the
  robot-runner. HOSTS LIVE ROBOTS: trades even when STL is down.
- **Robot runner** (`robot_runner/`) — Python, bundled to a single `robot-runner.exe`
  (PyInstaller) shipped INSIDE the agent release zip. Executes `trader/lab` strategies 1:1
  (same `STLRuntime` protocol) against local QUIK data via the agent's loopback gRPC
  bridge (127.0.0.1:50071). Persists robots/state next to the agent; auto-resumes.
- **Frontend** (`frontend/`) — Svelte 5 SPA (lightweight-charts + uplot), static assets via
  nginx; `/api` + `/ws` proxied to uvicorn. Per-robot showcase:
  `/?agent_robot=<robot_id>` (AgentRobotScreen).

Deploy target: a hoster (Ubuntu, ssh alias `hoster`, 83.69.248.175) running
`shectory-trader.service` (uvicorn :8000) + `shectory-ai46.service` (standalone team-46
paper strategy) + `shectory-optimizer.service`. App dir `~/apps/shectory-trader`.
Canonical remote `github.com/Shevbo/STL.git` (local remotes `origin`/`github` identical).
Agent build tree on the hoster: `~/quik_build` (synced by scp, Go toolchain userland).

## Architecture (the parts that span files)

**Two wire contracts are the center of gravity** (`proto/shectory/quik/v1/`):
- `quik_agent.proto` — agent↔STL stream: agent→STL `AgentMessage` (Register/Heartbeat/
  ticks/books/OrderUpdate/TransReply/LimitsState/RobotStatusReport), STL→agent
  `OrchestratorMessage` (Ack/PlaceOrder/CancelOrder/ReplaceOrder/KillSwitch/SetLimits/
  DeployRobot/UndeployRobot/SetRobotParams/PauseRobot/StartRobot).
- `runner_bridge.proto` — loopback agent↔runner: StreamTicks/StreamTape/StreamControl/
  StreamOrderEvents/PlaceRunnerOrder/ReportStatus.
Regenerate stubs after any edit (see the protobuf gotcha below).

**Market data (DDE is RETIRED):** `quik_agent/lua/shectory_trade.lua` publishes over the
file queue: ticks (getParamEx, 500ms), order books (getQuoteLevel2, 1s), the anonymized
all-trades tape (OnAllTrade, 300ms batches — the QUIK "Таблица всех сделок" window must
stay open), instrument params (price step/step cost/margin, 60s). The Go bridge routes
md/book/tape/param events into a `quikdde.Provider` overlay (freshest-wins merge with any
DDE sheets); the runner builds EXACT OHLCV bars from the tape (`robot_runner/bars.py`,
snapshot ticks muted while the tape flows). Operator config lives in a sidecar
`shectory_trade_config.lua` next to the script (survives script updates; example in
`quik_agent/lua/`). Synthetic prices are quantized to the instrument price step — never
draw or feed a price that cannot exist on the exchange.

**Robot hosting (LIVE robots run ON THE AGENT, not in STL):** STL deploys a `RobotSpec`
via `POST /api/v1/quik/robots/{id}/deploy-agent` (`trader/api/quik_robots.py`) → link
persists it in the agent's `robots/robots.json` (`internal/robots`) and relays to the
runner, which replays persisted specs on every reconnect (zero-touch resume after any
restart). Runner state (strategy dict, position, avg, realized P&L in PRICE POINTS, last
200 fills) persists in `runner_state.json`. Status mirrors back as `RobotStatusReport`
(incl. `signal_json` strategy introspection from `robot_runner/explain.py`) → STL
`QuikAgentStore` → `GET /api/v1/quik/robots-mirror` → showcase. KillSwitch = block new +
cancel working; positions stay open by design. The agent also hosts its own local
status+recon page (loopback `:8071`, `quik_agent/internal/status/page.html`) comparing
robots' claimed positions against QUIK's account tables; STL mirrors that JSON opaquely
at `GET /api/v1/quik/agent-local-status` for read-only remote viewing
(`frontend/public/agent-status.html`, same page, `?src=&interval=` params) — align/
manual-offset actions only work against the agent's own loopback, not the mirror.

**Order flow (human orders and robot orders share the tail):** UI → `POST
/api/v1/quik/orders/*` (`trader/api/quik_orders.py`) → limit check (`trader/quik/
limits.py`) → gRPC enqueue (`trader/quik/server.py`) → agent `trade.Manager` re-checks
limits → file-queue `C:\quik-bridge\cmd.jsonl` (prod QUIK has no LuaSocket) →
`shectory_trade.lua` `sendTransaction` → QUIK. Robot orders enter at the Manager via the
runner bridge with `client_id` prefix `rr:` (order events are fanned back only to `rr:`
subscribers). Replies flow back as OrderUpdate/TransReply; STL order state in
`trader/quik/orders.py`, market state in `trader/quik/store.py`.

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
per-robot execution context; `strategies/library.py` is the signal registry consumed 1:1
by backtest, STL robots AND the agent-side runner. The backtester + optimizer sweep jobs
live here too (self-healing orphan reaper in `api/app.py`).

**Process isolation rule (paid for twice):** no strategy/model computation ever runs
inside the STL API process. AI46 (`trader/lab/ai46/`) runs standalone: `python -m
trader.lab.ai46` under `shectory-ai46.service` with `AI46_ENABLED=0` in the API env
(in-process HMM re-fits blocked the event loop; py-spy proved it). Live robots run on the
QUIK agent for the same reason (an STL crash must not stop trading).

`trader/api/app.py` is the FastAPI app factory: its lifespan mounts routers, starts the
gRPC server, and launches background asyncio tasks (VDS fallback sweeper, orphan reaper,
QUIK order reconcile, Finam latency sampler → `trader/latency.py`). It is large — grep it,
don't read it whole. The `market_bars` endpoint has hot-path caches (ISS tail TTL +
agent_bars mtime parse cache) — keep them; removing them melted the box once.

## Commands

Single dev runner: `make` (bash/hoster/CI) or `dev.ps1` (Windows) — same verbs. Tools are
not auto-installed; `make check` reports the toolchain.

```bash
# Python (STL + robot_runner). Unit tests need no credentials.
poetry install
poetry run pytest -m "not integration" -q          # or: make test-py
poetry run pytest tests/runner/ -q                  # agent-side runner (bars/host/explain)
poetry run pytest tests/quik/test_store.py::test_pick_prefers_single_green_when_others_stale -q  # one test
poetry run pytest -m integration -q                 # needs FINAM_SECRET_TOKEN
poetry run ruff check trader/ tests/ robot_runner/  # lint (or: make lint)

# Go (QUIK agent). No Go toolchain locally — run ON THE HOSTER after scp'ing changed
# files into ~/quik_build (export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH).
cd quik_agent && go test ./...                      # or: make test-go
go test ./internal/trade/ -run TestReconcileStalePending
make gen                                            # regen Go + Python proto stubs (BOTH protos)
make build                                          # cross-build windows exe (amd64+386)

# Robot runner exe (PyInstaller cannot cross-compile — build ON WINDOWS):
bash deploy/build_runner.sh                         # -> dist/runner/robot-runner.exe

# Frontend (Svelte). Vite has trouble with the spaced path in Git Bash — build via node
# directly, or use PowerShell.
cd frontend && node ./node_modules/vite/bin/vite.js build
node ./node_modules/vitest/vitest.mjs run           # tests
```

## Deploy

- **SAFE deploy (default; `deploy/deploy.sh`'s remote `npm build` once thrashed the VDS
  into an operator hard-reboot):** build the frontend LOCALLY, then
  `git push` → `ssh hoster 'cd ~/apps/shectory-trader && git pull'` → scp
  `frontend/dist/index.html` + the new hashed `assets/index-*.js`/`.css` into
  `~/apps/shectory-trader/frontend/dist/`. Frontend-only = NO service restart (nginx
  serves dist). Backend change = `sudo systemctl restart shectory-trader` (drops the agent
  gRPC link briefly — agent redials; never restart while the operator live-tests trading).
- **QUIK agent + runner:** scp changed Go files + protos into hoster `~/quik_build`, then
  `bash ~/quik_build/publish_quik_agent.sh [agent_id]` — builds the agent exe, packs
  `robot-runner.exe` into the SAME zip when staged in `~/quik_build/quik_agent/dist/`
  (build it on Windows first, scp it up); the agent self-updates on start/03:00/command
  and its apply-.bat installs companion exes too. `build_rev` must be a NUMERIC epoch.
- **AI46:** `deploy/shectory-ai46.service`; runs `python -m trader.lab.ai46`.
- ssh aliases: `hoster` (prod), `smain` (federation/keymaster). No SSH to the QUIK VDS —
  anything there (QUIK settings, Lua script file, agent_config.json) is operator-only.

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
- **Live trading is human-initiated.** Do not arm `quik_trading_enabled` (dual flag: STL
  env AND the agent's `agent_config.json` — never pushed remotely), place real orders, or
  cut a robot over to real money without explicit operator permission. Cutover rule: never
  run the STL-side and agent-side variants of a robot on real money simultaneously.
- **Chart epoch bases differ.** `/api/v1/market/bars` (ISS) epochs are MSK-wall-clock
  stamped as UTC — render AS UTC, never add +3h again; fills are true-UTC and get +3h
  shifted onto that grid client-side. Finam `/chart/bars` REST is true UTC and has NO M30.
- **Runner P&L is in PRICE POINTS**, not rubles. Convert with the instrument point value
  (`coef = step_cost / price_step`, served by `/api/v1/quik/params` from the QLua feed).
- **VDS environment:** PyInstaller exes need the Universal CRT (vc_redist.x64) installed
  there; the VDS clock has drifted minutes before (`w32tm /resync`) — check the clock
  FIRST when data freshness looks wrong.
- **Agent flush discipline:** the link sends only CHANGED securities/params/ticks
  (poll_interval_sec=1 in prod); keep new frame types change-gated or STL CPU pays x5.

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
