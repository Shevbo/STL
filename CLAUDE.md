# CLAUDE.md

Guidance for Claude Code in this repository.

## Overview

Shectory Trade & Lab (STL) — live-trading platform for FORTS (MOEX derivatives). Five parts, one repo:

- `trader/` — Python 3.12 / FastAPI / asyncio backend: control-plane + monitor + Finam execution. NO heavy computation in-process (isolation rule below).
- `quik_agent/` — Go single-exe on a Windows VDS next to QUIK. Dials OUT over one bidi gRPC stream (no inbound port). HOSTS LIVE ROBOTS; trades even when STL is down.
- `robot_runner/` — Python → `robot-runner.exe` (PyInstaller) inside the agent zip. Runs `trader/lab` strategies 1:1 against local QUIK data.
- `frontend/` — Svelte 5 SPA, static via nginx. ONE robot stand serves real/paper/backtest (source chosen server-side).
- `companion/` — pure-Go tray panel `STLCompanion.exe`; page is `frontend/public/companion.html`, no exe rebuild for visual changes.

Deploy target: hoster `ssh hoster` (Ubuntu) — `shectory-trader.service` (:8000), `shectory-ai46.service`, `shectory-optimizer.service`. App dir `~/apps/shectory-trader`. Remote `github.com/Shevbo/STL.git`. Agent build tree `~/quik_build` (scp, NOT git).

## Find things — rag_search first

`rag_search(query, depth)` searches the indexed project material (docs, runbooks, architecture,
decision history, source) + the shared Shectory federation canon. Returns `index:path`, section title,
line numbers.

**Use it first.** Before `grep` / `find` / `Glob` / reading a file to *find out* — ask rag_search. Read files
directly only to edit, debug specific lines, or after rag_search points you there.

Depth ladder, cheap→expensive: `snippet` (default, top 5) → `chunk` (neighbours) → `file` (whole file) →
`context` (file + linked). Query with a real question or an exact symbol — retrieval is hybrid.

**`NO MATCH` means no match** (recall@3 is 82%, not proof of absence) — say so, read source, never invent.
Index is a snapshot; for files edited this session, trust the working tree. Served from `sdev`.

**If RAG is down:** it degrades to a keyword-only search (a banner says so). If the tool is missing
(`ssh sdev` down), fall back to `grep` / `Glob` / direct reads.

## Architecture

Full description: `docs/architecture.md`. Ask rag_search, don't read it whole. One rule stays here:

**Process isolation (paid for twice):** no strategy/model computation in the STL API process. AI46 runs
standalone (`shectory-ai46.service`, `AI46_ENABLED=0` in API env); live robots run on the QUIK agent.
An STL crash must not stop trading.

## Commands

Single runner: `make` (bash/hoster/CI) or `dev.ps1` (Windows) — same verbs. `make check` reports toolchain.

```bash
# Python
poetry run pytest -m "not integration" -q      # unit tests, no creds
poetry run ruff check trader/ tests/ robot_runner/

# Go agent — pb stubs generate ONLY on the hoster; build/test THERE after scp'ing changed files
# into ~/quik_build (export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH):
cd quik_agent && go test ./...

# Companion (pure Go, builds locally):
cd companion && CGO_ENABLED=0 go build -ldflags "-H=windowsgui -s -w" -o STLCompanion.exe .

# Runner exe (PyInstaller, Windows only): bash deploy/build_runner.sh

# Frontend (Vite dislikes the spaced path in Git Bash):
cd frontend && node ./node_modules/vite/bin/vite.js build
```

## Deploy

- **Frontend (safe, default):** build locally → `git push` → `ssh hoster 'cd ~/apps/shectory-trader && git pull'` → `bash deploy/deploy_dist.sh` (ships exactly the assets index.html references; no hand-picked files; no restart).
- **Backend:** `sudo systemctl restart shectory-trader` (drops the agent gRPC link briefly — never during a live operator test).
- **Agent + runner:** scp Go files + protos to `~/quik_build`, then `bash publish_quik_agent.sh --runner-sha <sha256> [agent_id]`. `build_rev` = numeric epoch; verify sha256 of the staged runner exe before and the zip entry after.
- **AI46:** `deploy/shectory-ai46.service`. No SSH to the QUIK VDS — operator-only.

Incident history: `docs/windows-incidents.md`.

## Windows — zones (STRICT, agreed 08.08.2026)

**FIRST know which window you are:** (1) `STL_WINDOW` env, (2) `CLAUDE.local.md` next to you. Not sure → do NOT edit code, ask the operator. Why strict: `docs/windows-incidents.md`.

**Edit only your zone; hand foreign-zone changes to their owner:**

| Window | Owns | Deploys |
|---|---|---|
| real trade | `proto/`, `quik_agent/`, `robot_runner/`, `~/quik_build`, the QUIK VDS | agent/runner releases |
| backtests | `trader/lab/`, `scripts/`, the i9, campaigns | nothing |
| UI & UX | `frontend/`, `companion/`, `trader/api/`, EVERY STL screen | frontend dist, `STLCompanion.exe` |

**Rules above zones:** arming, agent-release publication, and `shectory-trader` restarts happen ONLY from
real trade. Verify sha256 immediately before publish, not in advance.

**Locks in scripts (do not bypass):** `publish_quik_agent.sh` refuses unless `--runner-sha` matches the
staged file; `deploy_dist.sh` refuses while `frontend/` is uncommitted. A lock firing = the OTHER window
interfered: stop, re-check, re-stage; never "fix" the lock.

**Mail between windows:** inbox lands in a local file every 45s, a hook prints it. Setup once per window:
`STL_WINDOW=<your-window> bash scripts/devmail_install.sh`. Read/send: `ssh hoster 'bash
~/apps/shectory-trader/scripts/devmsg.sh …'` — details `docs/DEVMAIL.md`. Check your inbox at session start
and before «готово»; ack only what you did. **Send order: адресат ПЕРВЫМ, отправитель ПОСЛЕДНИМ** (flipped =
a letter to yourself from the neighbour). Mail older than 4h = overdue → tell the operator, never do a
neighbour's job silently.

## Critical gotchas

Full list (~24, several "bitten prod") in `docs/critical-gotchas.md` — rag_search finds them. Five to keep in your head:

- **QLua is 32-bit:** `string.format("%d", v)` truncates epoch-ms. Encode large ints with `%.0f`.
- **A params edit MERGES, never REPLACES** — unmentioned keys survive; writing the posted set verbatim wiped `exit_only`/`allow_short` on a REAL robot.
- **Protobuf 5.29:** regen Python stubs ONLY with `grpcio-tools<1.71`; ≥1.81 emits gencode 6.x that crash-loops prod.
- **Live trading is human-initiated** — never arm, place real orders, or cut over without explicit operator permission.
- **Runner P&L is in price points**, not rubles; convert via `coef = step_cost / price_step`.

## Style

- Read before writing; don't re-read unless changed; skip files >100KB unless required.
- Short, no filler/sycophancy, no emojis/em-dashes. Verify APIs/versions/SHAs by reading, don't guess.

---

## graphify

`graphify-out/` is stale since 09.08.2026 — do not `graphify update`. Use `rag_search` for structural and content questions.

<!-- onboarding-marker BEGIN — do not edit; rewritten by /onboarding -->
> **Federation onboarding** — канон: [.onboarding/CANONICAL.md](.onboarding/CANONICAL.md); карточка агента: [.onboarding/AGENT.md](.onboarding/AGENT.md).
> При старте сессии Claude обязан запустить `bash ~/.claude/skills/onboarding/bin/check_freshness.sh`. Если `status=stale` или `status=absent` — предложить пользователю `/onboarding` (без долгих объяснений, одна строка).
<!-- onboarding-marker END -->
