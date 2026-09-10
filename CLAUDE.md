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
  nginx; `/api` + `/ws` proxied to uvicorn. ONE robot stand serves real, paper and (next)
  backtest, source chosen SERVER-side — branching in the frontend produced drifted
  duplicate screens once, don't reintroduce it. Layout/nav/panel mechanics: `docs/architecture.md`.
- **Companion** (`companion/`) — pure-Go (NO cgo) Windows tray panel `STLCompanion.exe`:
  frameless always-on-top WebView2 window. The exe is only a shell; the PAGE is
  `frontend/public/companion.html` served from STL — visual changes deploy with the
  frontend, no exe rebuild. Auth: one-time pairing code -> long token, DPAPI-encrypted,
  opens EXACTLY ONE read-only endpoint. Drag/auth mechanics: `docs/architecture.md`.

Product docs live at `frontend/public/docs.html` («Справка → Документация платформы STL»,
5 разделов). Versioning: one date-based system release (e.g. `STL 2026.07.25`) shared by
platform + docs; satellites keep their build_rev but are mapped in the doc's version
table. When shipping a meaningful release, bump that block.

Deploy target: a hoster (Ubuntu, ssh alias `hoster`, 83.69.248.175) running
`shectory-trader.service` (uvicorn :8000) + `shectory-ai46.service` (standalone team-46
paper strategy) + `shectory-optimizer.service`. App dir `~/apps/shectory-trader`.
Canonical remote `github.com/Shevbo/STL.git` (local remotes `origin`/`github` identical).
Agent build tree on the hoster: `~/quik_build` (synced by scp, Go toolchain userland).

## Architecture

Full description: `docs/architecture.md` (wire contracts, market data, robot hosting,
order flow, runner parity, dual-trading safety, restart immunity, broker abstraction,
Lab, market-session oracle, smart orders, robot stand, time semantics, param sweeps).
Ask `rag_search` rather than reading it whole.

One rule from it stays here because everything else depends on it:

**Process isolation rule (paid for twice):** no strategy/model computation ever runs
inside the STL API process. AI46 (`trader/lab/ai46/`) runs standalone: `python -m
trader.lab.ai46` under `shectory-ai46.service` with `AI46_ENABLED=0` in the API env
(in-process HMM re-fits blocked the event loop; py-spy proved it). Live robots run on the
QUIK agent for the same reason (an STL crash must not stop trading).

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

# Go (QUIK agent). Local Go exists, but the agent's internal/pb stubs are generated
# ONLY on the hoster — build/test the agent THERE after scp'ing changed files into
# ~/quik_build (export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH).
cd quik_agent && go test ./...                      # or: make test-go
go test ./internal/trade/ -run TestReconcileStalePending
make gen                                            # regen Go + Python proto stubs (BOTH protos)
make build                                          # cross-build windows exe (amd64+386)

# Companion (pure Go, no cgo, builds LOCALLY; exe is gitignored):
cd companion && CGO_ENABLED=0 go build -ldflags "-H=windowsgui -s -w" -o STLCompanion.exe .

# Robot runner exe (PyInstaller cannot cross-compile — build ON WINDOWS):
bash deploy/build_runner.sh                         # -> dist/runner/robot-runner.exe

# Frontend (Svelte). Vite has trouble with the spaced path in Git Bash — build via node
# directly, or use PowerShell.
cd frontend && node ./node_modules/vite/bin/vite.js build
node ./node_modules/vitest/vitest.mjs run           # tests
node ./node_modules/vitest/vitest.mjs run src/lib/lab-analytics.flat.test.ts   # one file

# Robot stand / params-frame invariants (run these when touching either):
poetry run pytest tests/lab/test_signal_both_sides.py -q    # ни одна стратегия не заперта в одну сторону
poetry run pytest tests/lab/test_macd_warmup.py -q          # окно прогрева покрывает свой период
poetry run pytest tests/quik/test_params_merge.py -q        # правка параметров не стирает чужие ключи
cd frontend && node ./node_modules/vitest/vitest.mjs run src/lib/param-groups.test.ts  # фрейм не теряет поле

# Ad-hoc backtest of a live config (answering "what would X have done"): run it ON THE
# HOSTER — MOEX ISS is unreachable from the dev box (httpx ConnectTimeout), and the i9
# queue is for sweeps. load_bars_iss + run_single_backtest directly, one combo at a time,
# `nice -n 15` (shared box). To try UNCOMMITTED strategy code there, load the patched
# library by PATH (importlib) instead of overwriting the prod file.

# Portal-authed API from a shell (mirror/status/orders endpoints) — mint a session
# Bearer ON THE HOSTER with the app's own signer (never print the secret):
ssh hoster 'cd ~/apps/shectory-trader && set -a; . ~/.shectory_trade.env; set +a; \
  PY=$(/home/ubuntu/.local/bin/poetry env info --path)/bin/python; \
  TK=$($PY -c "import os;from trader.auth.portal import make_session_token as m;print(m(\"<email>\",os.environ[\"SHECTORY_AUTH_BRIDGE_SECRET\"]))"); \
  curl -s -H "Authorization: Bearer $TK" localhost:8000/api/v1/quik/robots-mirror'
```

## Deploy

- **SAFE deploy (default):** build the frontend LOCALLY, then `git push` →
  `ssh hoster 'cd ~/apps/shectory-trader && git pull'` → **`bash deploy/deploy_dist.sh`**
  — it ships index.html + exactly the assets index.html references and then re-reads
  them THROUGH nginx, failing loudly on a 404. Never hand-pick asset files. Frontend-only
  = NO service restart (nginx serves dist). Backend change = `sudo systemctl restart
  shectory-trader` (drops the agent gRPC link briefly — agent redials; never restart
  while the operator live-tests trading). Assets are hashed + `Cache-Control: immutable`
  1y; if a cold first-load is slow, check nginx `gzip_types` is UNcommented in
  `/etc/nginx/nginx.conf`. Incident history: `docs/windows-incidents.md`.
- **QUIK agent + runner:** scp changed Go files + protos into hoster `~/quik_build`, then
  `bash ~/quik_build/publish_quik_agent.sh --runner-sha <sha256> [agent_id]` (the sha of
  YOUR local runner build; required whenever a runner is staged — three-window lock, see
  above) — builds the agent exe, packs `robot-runner.exe` into the SAME zip when staged
  in `~/quik_build/quik_agent/dist/` (build it on Windows first, scp it up); the agent
  self-updates on start/03:00/command and its apply-.bat installs companion exes too.
  `build_rev` must be a NUMERIC epoch. Omit `[agent_id]` to publish WITHOUT triggering —
  the agent applies it at 03:00. **VERIFY `sha256sum` of the staged runner exe against
  the local build BEFORE publishing, and the zip entry after** (python3 zipfile; no
  unzip on the hoster). Incident history: `docs/windows-incidents.md`.
- **AI46:** `deploy/shectory-ai46.service`; runs `python -m trader.lab.ai46`.
- ssh aliases: `hoster` (prod), `smain` (federation/keymaster). No SSH to the QUIK VDS —
  anything there (QUIK settings, Lua script file, agent_config.json) is operator-only.

## Three parallel Claude windows (STRICT, agreed 08.08.2026)

**СНАЧАЛА УЗНАЙ, КАКОЕ ТЫ ОКНО.** Порядок опознания: (1) переменная `STL_WINDOW`, с
ней окна и запускаются; (2) файл `CLAUDE.local.md` рядом с этим — он лежит только в
тех клонах, где окно одно на весь каталог. Не опознал себя — НЕ ПРАВЬ КОД, спроси
оператора. Инциденты, из-за которых это правило строгое: `docs/windows-incidents.md`.

**Zone ownership — edit only your zone; a change needed in a foreign zone is HANDED
to its owner, not made:**

| Window | Owns | Deploys |
|---|---|---|
| real trade | `proto/`, `quik_agent/`, `robot_runner/`, `~/quik_build`, the QUIK VDS | agent/runner releases |
| backtests | `trader/lab/`, `scripts/`, the i9, campaigns | nothing |
| UI & UX | `frontend/`, `companion/`, `trader/api/`, and EVERY STL screen | frontend dist, `STLCompanion.exe` to the operator's share |

**UI & UX owns how STL LOOKS, everywhere it has a screen:** the SPA, every standalone
page in `frontend/public/` (companion.html, m.html, docs.html, agent-status.html,
watchdog-log.html), and the companion tray shell `companion/` — one thing split
across two languages, don't split it between windows. No trading logic in it: its
token opens a single read-only endpoint.

The one screen that straddles a border is the agent's own local page
`quik_agent/internal/status/page.html`. Its LAYOUT is UI & UX like any other screen;
the status JSON behind it is built in Go by real trade, who also ships the file inside
the agent release. Change the markup, hand the delivery over — never scp into
`~/quik_build` from this window.

**Почта между окнами.** Фоновый процесс кладёт входящие в локальный файл раз в 45 с;
хук печатает новое на каждом твоём вводе, без сети. Установка (один раз на окно):

```bash
STL_WINDOW=real-trade bash scripts/devmail_install.sh
```

Подробно: [docs/DEVMAIL.md](docs/DEVMAIL.md).

```bash
# Свой ящик (подставь СВОЁ окно):
ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh inbox real-trade'

# Отправка. ПОРЯДОК: адресат ПЕРВЫМ, отправитель ПОСЛЕДНИМ — перепутав их,
# отправляешь письмо САМОМУ СЕБЕ от имени соседа. Ниже: ui-ux пишет real-trade.
ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh send real-trade "тема" "текст" ui-ux'

ssh hoster 'bash ~/apps/shectory-trader/scripts/devmsg.sh ack <id>'   # прочитано
```

Окна: `real-trade`, `backtests`, `ui-ux`, `operator`, плюс `all` — всем сразу.
ОБЯЗАННОСТЬ каждой сессии: проверить свой ящик в начале работы и перед «готово».
Письмо без `ack` остаётся непрочитанным и достанется следующей сессии — подтверждай
только сделанное. Письмо старше 4 часов помечается просроченным и печатается
автоматически при чтении почты. **Реакция на просрочку ровно одна: сказать
оператору** — он единственный, кто откроет молчащее окно. Сделать чужую работу
молча вместо эскалации ЗАПРЕЩЕНО. История поломок этого механизма: `docs/windows-incidents.md`.

**Rules above zones:** arming, agent-release publication, and `shectory-trader`
restarts happen ONLY from the real trade window, no matter who wrote the code.
Before touching a staged trading binary: check robot state and verify sha256
IMMEDIATELY before publish, not in advance.

**Locks in scripts (do not bypass):** `publish_quik_agent.sh` refuses to run when a
runner is staged unless `--runner-sha <sha256-of-YOUR-build>` matches the staged
file (prints staged mtime/owner so an overwrite is visible). `deploy_dist.sh`
refuses to deploy while `frontend/` has uncommitted changes — prod carries exactly
what git carries. A lock firing means the OTHER window interfered: stop, re-check,
re-stage your own artifact; never "fix" the lock.

## Critical gotchas

- **QUIK QLua is 32-bit: `string.format("%d", v)` TRUNCATES epoch-ms (has bitten prod).**
  The terminal's Lua casts `%d` through a 32-bit C long, so any integer >= 2^31 (13-digit
  epoch-ms: pong t0, `last_trade_ts_ms`, trade timestamps) is silently corrupted/zeroed on
  the wire. Encode large ints as `string.format("%.0f", v)` (`shectory_trade.lua` json.encode).
  Also: the hand-rolled encoder emits an empty Lua table as `{}`, but the Go bridge decodes
  `rows` into `[][]any` and DROPS a JSON `{}` — empty arrays must serialize as `[]`
  (`if is_array then`, not `and n > 0`). Both bugs made a flat account's recon read STALE.
  And measure agent<->QUIK RTT on the AGENT clock alone (record the ping send time locally),
  never the Lua-echoed t0.
- **A params edit MERGES, it never REPLACES** (`relay_robot_params`,
  `trader/api/quik_robots.py`). The editor form is built from the strategy's
  `params_schema`, and INFRASTRUCTURE flags are deliberately not in it (`exit_only`,
  `bar_offset_min`) — so writing the posted set verbatim WIPED them. 05.08.2026 the
  operator changed qty/avg_max on a REAL robot and silently lost `exit_only=true` and
  `allow_short=0`: the robot left exit-only, regained both sides and opened a contract
  before anyone noticed. Incoming keys win (a flag can still be set to 0), unmentioned
  keys survive, broken JSON never blanks a robot. The stand shows every param the robot
  ACTUALLY carries (schema ∪ params_json, extras badged «служебный») plus a
  «Уедет роботу» diff over ALL keys — an invisible parameter cannot be reviewed.
  Params UI work has its own project skill: `/params_UI <robot>`.
- **Manual i9 runs PREEMPT, and priority alone does not do that.** `priority DESC`
  only orders the QUEUE; a claimed campaign holds the pool for its whole length (3072
  combos ≈ 90 min). The agent therefore builds its pool with `workers + 1` and a
  separate `_manual_loop` claims with `min_priority=100` onto that RESERVED worker
  while a campaign or a generic task runs (both block the claim loop). Side runs
  publish no progress — the progress queue is shared and their combos would be counted
  into the campaign's «сделано» and leak into its live top. Anything larger than
  `MANUAL_SIDE_MAX_COMBOS` is handed back via `POST /api/v1/agent/release`: on one
  worker a big sweep is slower than waiting for the full pool. Agent liveness is judged
  by the `i9_heartbeat` freshness (4s) — `/claim` polling and `claimed_at` both go stale
  during a long job and the UI showed «i9 ОФЛАЙН» on a perfectly healthy agent.
- **A leaderboard row older than the last `library.py` change is history, not an
  estimate.** `optimization_leaderboard` keeps 3.5M rows for years while strategy code
  moves under them: `camp-20260731-shectory1w`'s leader re-runs today at +278k instead
  of the recorded +529k (the 31.07 разножка change doubled its entries). `verified_at`
  marks rows recomputed by current code; the Botstore chart warns in place when the
  recomputed net differs by >2%. Rows with NO window (`date_from` NULL, ~15k of the
  visible top-50) can never be verified — the period they were run on was not recorded.
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
  The AGENT RUNNER's tape-built bars are TRUE UTC — see "Strategy time semantics" (Lab)
  before running any wall-clock-anchored strategy on the agent.
- **Runner P&L is in PRICE POINTS**, not rubles. Convert with the instrument point value
  (`coef = step_cost / price_step`, served by `/api/v1/quik/params` from the QLua feed).
- **The QLua/ISS `margin` is the EXCHANGE's ГО, not what the account pays.** The broker
  charges a multiple (30.07.2026, RIU6: exchange 22 375 ₽, account 53 672 ₽ = 2.4x), so
  every report built on the feed value understated capital and overstated «доходность в
  год» by exactly that factor. `QUIK_MARGIN_MULTIPLIER` (default 1.0) is applied where
  exchange margin becomes money for reporting: the companion snapshot and the robot card
  (shipped to the UI as `margin_multiplier` in `/api/v1/quik/params`). It is a REPORTING
  correction only — nothing sizes orders off it. The truth for the account is
  `cbplused`/`cbplplanned` in the agent's money block.
- **VDS environment:** PyInstaller exes need the Universal CRT (vc_redist.x64) installed
  there; the VDS clock has drifted minutes before — the agent now runs `w32tm /resync`
  hourly itself (main.go), but still check the clock FIRST when freshness looks wrong.
- **Never claim "market closed" from the calendar.** FORTS trades weekends/holidays on
  its own schedules; a weekend problem is as urgent as a weekday one (session opens
  07:00 MSK daily). Read `GET /api/v1/quik/market-session` (ISS SYSTIME oracle) — a
  frozen tape with the market CLOSED is normal; with it OPEN and `last=0` in the feed it
  means the QUIK «Таблица всех сделок» window is closed (operator must open it).
- **WebView2 in the companion (both verified on Win10 19042):** the controller will NOT
  be created on a `WS_EX_LAYERED` parent (comes back nil → crash), and the DWM acrylic
  accent (state 4) renders the window fully INVISIBLE — use accent 2
  (TRANSPARENTGRADIENT). Translucency = DWM composition, not layered windows.
- **i9 runs a hand-synced repo copy** (self-update via `agent/update_manifest.txt`,
  RAW_BASE raw.githubusercontent — intermittently RST-blocked from that network, jsdelivr
  mirror serves the same bytes). NEVER leave strategy logic only on the i9: the original
  `__inv` inversion lived there uncommitted, got wiped by a resync, and left the
  leaderboard with unreproducible numbers. Everything the i9 executes must be in git +
  the manifest. Trigger self-update: `INSERT INTO agent_control(key,value)
  VALUES('update_token', now()::text) ON CONFLICT (key) DO UPDATE ...`.
- **The watchdog probe lives ON the hoster** (`~/stl-watchdog-probe.sh` + morning resume
  `~/stl-morning-resume.sh`, auto-pause marker `~/.stl-autopaused`) — NOT in the repo.
  Repo-side session-oracle changes don't reach it until the hoster script is patched too.
- **Live-robot equity curves come from the LEDGER (`algo_trades`), never from replaying
  the 200-fill mirror tail** — the tail starts mid-position and its fee model double-
  counted entry fees on partial exits (drew −103k where the journal said +129k). The
  chart takes `closeSeries` from the journal; with no journal it draws NOTHING plus an
  honest note (a drawn lie looks like truth). `tradeEvents` fee share on partial closes
  is pinned by `lab-analytics.fees.test.ts`.
- **One classifier, one fill set, one fee model — or the stand contradicts itself.** The
  chart markers and the «Сделки робота» table both label TP/SL/AVG through `tradeEvents`,
  so any divergence in their INPUTS shows up as the same trade labelled two ways (both
  seen live 30.07.2026). Three invariants: (1) the journal window must start from a
  PROVABLE flat — `fromLastFlat` trims to the last `pos_after == 0`, because the fetch is
  capped (`limit=1000`) and a robot that outgrew it silently fell back to the mid-position
  tail replay and painted profitable closes as SL; (2) fills are grouped by ORDER
  (`groupByOrder`) — the journal stores a row per QUIK trade, and one order filling 1+1+1+2
  read as OPEN plus three phantom «усреднений»; (3) agent robots cross the spread, so the
  fee model is TAKER everywhere — the ledger (`commission_for(..., taker=True)`) and the
  runner (`taker_points`) already are, the chart's old maker default was legacy from the
  human maker engine.
- **Agent flush discipline:** the link sends only CHANGED securities/params/ticks
  (poll_interval_sec=1 in prod); keep new frame types change-gated or STL CPU pays x5.
  Gate by CONTENT when the publisher re-stamps unchanged data every cycle (the Lua book
  ts advances every second — a timestamp gate passes everything).
- **Lua on the VDS runs from MEMORY.** Copying a newer `shectory_trade.lua` over the file
  changes nothing until the operator stops/starts the script in QUIK (Сервисы →
  Lua-скрипты). File mtime identical to repo ≠ the running code is current. The agent
  installs the script under a VERSION-stamped name (`shectory_trade_v<SCRIPT_VERSION>.lua`
  from the file's own constant) so the load dialog shows WHICH build the operator picks.
  Optional row columns are how Lua/agent stay compatible across versions: acc_pos row 4
  = per-instrument varmargin, acc_money row 6 = cbplused («Тек. чист. поз.») — a null in
  the UI means «старый Lua ещё запущен», not a bug (v2026.07.24-posvm ships both).
- **Never restart the STL service mid-diagnosis of the mirror**: `robots-mirror` /
  `agent-local-status` are in-memory mirrors — a restart empties them until the agent
  redials and re-reports (~15-60s); an empty mirror right after a restart is not an
  outage, and robots[0] indexing will throw.
- **Robot order price is the RUNNER's, and it MUST land on the exchange step grid.** Robot
  orders go marketable via `robot_runner/runtime.py` (fresh quote → SELL at the bid / BUY at
  the ask; stale quote → cross by `_STALE_CROSS_FRAC`), NOT the agent maker engine
  (`internal/trade/execution.go` StartExecution is human/explicit orders only —
  `PlaceRunnerOrder` uses the plain `Orders.PlaceOrder`). The price must be a MULTIPLE of the
  instrument price step; the RUNNER does not know the step, only the agent does (`PriceStep`
  from the QLua params). An off-grid price is rejected by QUIK ("Неправильно указана цена …
  не кратно шагу", TransReply status -1), never becomes active, and the robot re-emits it
  every bar with zero fills — the position HANGS (2026-07-21: a cushioned 83533.12 off a
  10-pt grid rejected every real order). Separately, SELL exactly at the bid RESTS when the
  touch ticks away during exchange lag (~0.4-1.3s) in a fast market — the exit does not
  cross. Any change to the marketable price must quantize to the step; put the quantization
  on the AGENT (it alone knows PriceStep).
- **A standalone-module strategy shows "стратегия X не найдена" in the signal box —
  COSMETIC, not a break.** `robot_runner/explain.py` introspects only REGISTRY strategies
  (+ a dedicated `_fvg_explain`); a standalone module (us_open_fvg, donchian_breakout, …) has
  no registry entry, so the «Сигнал сейчас» box prints "не найдена". The robot STILL trades —
  `host.resolve_on_bar` imports the module for `on_bar`.
- **`record-fill-agent` (log an operator's by-hand close into the runner) leaves a TRANSIENT
  recon trade-mismatch.** It fabricates a fill that realizes P&L + fixes the runner's position
  but carries no tagged QUIK trade, so recon reads that robot "сделки не сходятся" until the
  fill ages past MSK-midnight (the matcher is session-scoped). Benign — position is correct.
  Gated: robot PAUSED + confirm_id == robot_id. Change a robot's trading WINDOW the same
  family way: `POST …/robots/{id}/params` with a `schedule` field → full DeployRobot
  re-deploy from the mirror echo (zero-loss; send `params_json` VERBATIM or you overwrite the
  strategy). FORTS morning session opens 07:00 MSK; live window `07:00-23:50` (clearing 23:50).
- **The hoster is a SHARED, resource-tight box.** Besides STL it runs `shectory-optimizer` +
  `shectory-ai46` + a PM2 fleet of UNRELATED Node apps (komissionka, ourdiary, garden-manager,
  eschool, bots) + Postgres. `earlyoom` SIGKILLs under memory pressure — STL (uvicorn) can
  crash-loop OOM (`status=9/KILL` right after `lab.scheduler.robot_started`), and Restart can
  leave it dead → needs a manual `sudo systemctl start shectory-trader`. A heavy Go
  cross-build/sweep, or a full disk, on the hoster can tip STL over. To recover: free
  memory/disk or `systemctl stop shectory-optimizer`; do NOT mass-kill the operator's OTHER
  apps — server admin is the operator's call, report and let them decide. Related: publishing
  the agent rebuilds the WHOLE agent from `~/quik_build` (a loose scp tree, NOT git) — any
  uncommitted change there ships, so publish only from a verified-clean tree.

## Style
- Read existing files before writing; don't re-read unless changed. Skip files over 100KB unless required.
- Short, concise, no filler or sycophancy. No emojis or em-dashes. Verify APIs/versions/SHAs by reading, don't guess.

---

## graphify

`graphify-out/` is stale since 09.08.2026 — do not run `graphify update`. Use `rag_search`
for both structural and content questions until it is rebuilt.

## rag_search — find things instead of loading them

`rag_search(query, depth)` searches the indexed project material (docs, runbooks,
architecture decisions, decision history, trading library, tests) plus the shared Shectory
federation canon. It returns plain text with `index:path`, section title and line numbers.

**Use it first.** Before `grep`, `find`, `Glob` or reading a file to *find out* something —
ask rag_search. Read files directly only to edit them, to debug specific lines, or after
rag_search has pointed you at the file.

**Escalate depth, never start deep:** `snippet` (default, top 5, cheapest) → `chunk`
(neighbours, when a snippet is cut mid-thought) → `file` (whole file, only to edit/reason
about it fully) → `context` (file + linked files, for "how does this flow end to end").
Query with a real question or an exact symbol — retrieval is hybrid.

**`NO MATCH` means no match** (measured recall@3 is 82%, not proof of absence) — say so and
read source files, never invent. Index is a snapshot; for files edited this session, trust
the working tree. Served from `sdev`, not the live trading VDS.

**If RAG is down:** `rag_search` degrades to a keyword-only search and says so in a banner.
If the tool itself is missing (`ssh sdev` down), use `grep` / `Glob` / read files directly.


<!-- onboarding-marker BEGIN — do not edit; rewritten by /onboarding -->
> **Federation onboarding** — канон: [.onboarding/CANONICAL.md](.onboarding/CANONICAL.md); карточка агента: [.onboarding/AGENT.md](.onboarding/AGENT.md).
> При старте сессии Claude обязан запустить `bash ~/.claude/skills/onboarding/bin/check_freshness.sh`. Если `status=stale` или `status=absent` — предложить пользователю `/onboarding` (без долгих объяснений, одна строка).
<!-- onboarding-marker END -->
