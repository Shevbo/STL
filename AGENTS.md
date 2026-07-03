# AGENTS.md — operating + harness doc

Ground truth for any coding agent / orchestrator (Cursor, Codex, portal
`pm-harness-runner`) working in this repo. Architecture + conventions live in `CLAUDE.md`;
this file is the **operate / verify / guardrails** contract. **This is a LIVE trading
system on a real broker account — read Guardrails before doing anything.**

## Workspace

- Absolute path (dev): `C:\Dev\Shectory Trade & Lab` (Git Bash: `/c/Dev/Shectory Trade & Lab`).
- Git work-tree; keep a clean baseline so changes are revertible (`git checkout .` /
  `git clean -fd`). Commit per subtask.
- Prod checkout: hoster `~/apps/shectory-trader` (ssh alias `hoster`, 83.69.248.175).
  Remotes `origin` and `github` both = `github.com/Shevbo/STL.git`.
- The path has spaces + `&`: Vite fails from Git Bash — build the frontend via
  `node .\node_modules\vite\bin\vite.js build` (PowerShell) or on the hoster.

## Operate (copy-pasteable)

```bash
# Python STL (backend). No creds needed for unit tests.
poetry install
FINAM_SECRET_TOKEN=dummy python -m pytest -m "not integration" -q   # or: make test-py
python -m pytest tests/quik/test_store.py::test_pick_prefers_single_green_when_others_stale -q  # one test
python -m ruff check trader/ tests/                                 # lint

# Frontend (Svelte). Build via node (spaced path breaks the npm-script shim in Bash).
cd frontend && node ./node_modules/vitest/vitest.mjs run            # tests (npm test)
node ./node_modules/vite/bin/vite.js build                         # build

# Go QUIK agent. No Go toolchain locally — run on the hoster (PATH already set there).
cd quik_agent && go test ./...                                      # or: make test-go
make gen        # regen proto stubs (Go + Python)  — see protobuf gotcha in Guardrails
make build      # cross-build windows exe (amd64+386)
```

## Verify loop + baseline

The orchestrator accepts work only when these stay green (or no redder than baseline):

| Command | Baseline (2026-07-03) |
|---|---|
| `python -m ruff check trader/ tests/` | **0 errors** ("All checks passed!") |
| `python -m pytest -m "not integration" -q` | **335 passed**, 9 deselected (integration needs FINAM_SECRET_TOKEN) |
| `cd frontend && node ./node_modules/vitest/vitest.mjs run` | **24 passed** (5 files) |
| `go test ./...` in `~/quik_build/quik_agent` ON THE HOSTER | green — `export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH` first (plain ssh has no go) |

Active plan: `docs/superpowers/plans/2026-07-03-quik-side-robot-agent.md` (12 tasks). Its
NEW verify loops as they come online: `python -m pytest tests/runner/ -q` (robot_runner),
`go test ./internal/robots/ ./internal/runner/` (agent robot hosting). Go work cycle: edit
locally in `quik_agent/`, scp the changed files (and proto) into hoster `~/quik_build/`,
test there, commit locally.

"Inherited red" vs "I broke it": the above is the green baseline — a failure you did not
introduce is not a reason to stop, but never leave the base redder than you found it.

## Map (JIT retrieval, don't dump the repo)

- `trader/` — STL backend. `api/app.py` = FastAPI factory + lifespan (grep it, ~3k lines);
  `api/quik_*.py` = QUIK routes; `quik/` = agent gRPC link (server/store/orders/limits);
  `broker/` = BrokerInterface + adapters; `lab/` = robots + backtester; `auth/`, `config.py`.
- `quik_agent/` — Go agent. `internal/{link,trade,quikdde,selfupdate,health,watchdog}`.
- `frontend/src/` — Svelte SPA. `components/`, `lib/`, `lib/stores/*.svelte.ts`.
- `proto/shectory/quik/v1/quik_agent.proto` — the agent↔STL wire contract (center of gravity).
- `deploy/` — `deploy.sh` (STL), `publish_quik_agent.sh` (agent), `nginx.conf`, systemd unit.
- `docs/quik-trading-startup.md` — the trading startup runbook. `memory/` — session memory.

## Guardrails (HITL — a human confirms; never auto-run)

This repo trades real money. The following are IRREVERSIBLE / OUTWARD-FACING — an
orchestrator must STOP and get explicit operator confirmation, never do them to "make
progress":

1. **Arming trading** — `quik_trading_enabled` (STL env) or the agent's `agent_config.json`
   flag. Dual flag: BOTH must be true. Never arm autonomously.
2. **Placing / cancelling REAL orders**, running the 1b maker loop live. Human-initiated
   only, kill-switch ready, 1 contract, far limits for tests. 1b has a runaway history.
3. **Prod deploy / service restart** — `deploy.sh`, `systemctl restart shectory-trader`.
   A backend restart DROPS the agent gRPC link; **never restart the backend while the
   operator is live-testing trading**. Frontend-only changes: rebuild `frontend/dist` on
   the hoster, no restart.
4. **Agent rebuild + self-update** — `publish_quik_agent.sh` + the self-update trigger
   restart the LIVE agent (and can desync the QUIK Lua file-queue → a manual Lua reload).
   Coordinate the restart with the operator.
5. **DB writes on the shared hoster Postgres** — robot roll/retire (`robots` table),
   migrations, sweeps. (An RIM6→RIU6 robot roll was correctly blocked pending consent.)
6. **Secrets** — Finam / agent / TG / portal tokens come from keymaster (`ssh smain`) or
   env; never hardcode or print secret VALUES (env name + path only). Do not create/write
   secrets autonomously.
7. **`git push`, force-push, `rm -rf`** and anything touching prod data / webhooks.

Federation gates (this repo contains agent/LLM code — AI46, Lineman): Claude via **OAuth
subscription only** (never a pay-per-token key); **all LLM traffic via Lineman** (no direct
vendor egress). Note them, don't break them.

## Conventions

- **Commit per subtask**; conventional messages; end with the Co-Authored-By trailer.
- **Protobuf 5.29 (bit prod twice):** regen PYTHON stubs only with `grpcio-tools<1.71`
  (header must read "Protobuf Python Version: 5.29.0"); ≥1.81 emits gencode 6.x → STL
  crash-loops on import in prod while passing locally. Verify the header before committing.
- **Chart time is MSK** via `frontend/src/lib/chart-time.ts` (never shift the data).
- **RTK**: shell commands are auto-prefixed `rtk` by a hook (token optimizer).
- Deploy frontend without a backend restart when possible (nginx serves `frontend/dist`).

## Backlog — "launch real trading" (from the operator; HITL-gated)

Autonomy legend: **[auto]** safe code + verify (frontend deploy / build+test, no live-trade
side effect) · **[coord]** code is autonomous but activation needs an operator-timed restart
· **[HITL]** human decision / secret / live-trade — do NOT auto-execute.

1. **[HITL]** Telegram alerts — `QUIK_ALERT_TG_TOKEN/CHAT_ID` unset (verified in prod logs);
   critical events (DDE down, kill-switch, stuck order) do not reach the operator. Env +
   restart. Needs the operator's TG token/chat id.
2. **[coord]** Agent alert "sent a transaction, no trans_reply/order in N s" → visible
   "QUIK/Lua not responding" signal (turns silent phantoms into an alert). Go agent code
   (auto-writable + `go test`), activation = coordinated agent self-update.
3. **[auto]** Preflight/readiness panel: one place aggregating agent green + DDE/QUIK UP +
   whitelist synced + both flags — client-side over existing `/status` + `/orders/config`
   (no backend restart).
4. **[coord]** QuikBroker positions/account reporting from QUIK (make `is_trade_ready()`
   pass) — proto + agent DDE read + STL. Larger; agent restart to activate.
5. **[coord]** Migrate robots (AI46/Lab) onto `BrokerInterface` (they trade Finam directly
   today).
6. **[HITL]** 1b maker live re-test, 1 contract, kill-switch ready.
7. **[HITL]** Production limits — `max_working_contracts`/`max_contracts_per_order` are test
   values (2/2). Operator decides live values; env + restart.
8. **[auto]** Performance: login ~90s, 1m chart ~75s, backtest screen ~180s. Investigate +
   fix (frontend/backend; backend fixes need a coordinated restart).
9. **[coord]** Automatic contract rollover (today only the `liveify` display roll M6→U6).
10. **[coord]** Instrument-params panel (ГО/step) for QUIK instruments (agent must report them).
11. **[HITL]** Finish the operator QA checklist at `/qa` (manual verification by the operator).

Minimal path to reliable manual real trading = **1 + 2 + 3**.

## Scratch / notes

Agent working notes for a long task go in `.harness/notes/` (git-ignored). The recorded
verify baseline lives in `.harness/baseline.md`.
