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
  nginx; `/api` + `/ws` proxied to uvicorn. ONE robot stand serves real, paper and
  (next) backtest: `AgentRobotScreen` is the stand, `RobotWindow` is a 50-line modal
  wrapper around it, and the SOURCE is chosen SERVER-side by
  `GET /api/v1/lab/robot-stand/{id}` (agent robot from the mirror, STL paper robot
  assembled by `trader/lab/robot_stand.py` into the SAME mirror-shaped record).
  Branching in the frontend is what produced two screens that then drifted apart —
  don't reintroduce it. The response carries `caps` (quik/chat/commands/signal): a
  frame the source can't support is NOT RENDERED, never rendered empty. Per-robot
  showcase: `/?agent_robot=<robot_id>`, modal `/?lab=live&robot_win=<id>`. Two-level burger nav (`NavMenu.svelte`)
  is mounted on EVERY screen; deep links `?orders=1`/`?tables=1`/`?equity=1` open frames.
  Panels are `Frame.svelte` (collapse + maximize via a shared `maxId`) split by
  `Splitter.svelte`, each size persisted per layout profile. The browser tab title is set
  from what is open (`lib/page-title.ts`) — content first, product suffix last, since a
  narrow tab shows only the first characters; a full-page screen owns its own title and
  the shell must not overwrite it.
- **Companion** (`companion/`) — pure-Go (NO cgo) Windows tray panel `STLCompanion.exe`:
  frameless always-on-top WebView2 window bottom-right. The exe is only a shell (tray,
  window, token, loopback proxy); the PAGE is `frontend/public/companion.html` served
  from STL — visual changes deploy with the frontend, no exe rebuild. Auth: one-time
  pairing code (issued on /watchdog-log.html) -> long token, DPAPI-encrypted per Windows
  account; the token opens EXACTLY ONE endpoint (`GET /api/v1/quik/companion/snapshot`,
  read-only) — pinned by tests in `tests/quik/test_companion.py`. The panel is DRAGGABLE
  by any free spot (NOT by a header strip — the panel's content is rebuilt from each
  snapshot, so a handle bound to one row eventually disappears with it; a 3px slop
  threshold keeps plain clicks native). That one feature spans both halves: the page
  sends incremental mouse deltas to loopback `/move`, the exe moves the window and
  remembers the spot in
  `config.json` (`placed`/`pos_x`/`pos_y`, tray item «Вернуть в правый нижний угол»
  resets it). A frameless WebView2 window CANNOT use the system title-bar drag — the
  mouse belongs to the WebView2 child window, so WM_NCHITTEST never reaches us. Deltas
  are scaled by `devicePixelRatio` (the page measures CSS px, the window lives in
  physical px) and the saved origin is always re-clamped to the VIRTUAL screen (all
  monitors) so an unplugged monitor cannot hide the panel.

Product docs live at `frontend/public/docs.html` («Справка → Документация платформы STL»,
5 разделов). Versioning: one date-based system release (e.g. `STL 2026.07.25`) shared by
platform + docs; satellites keep their build_rev but are mapped in the doc's version
table. When shipping a meaningful release, bump that block.

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

**Market data (DDE is RETIRED, default-off in code):** `quik_agent/lua/shectory_trade.lua`
publishes over the file queue: ticks (getParamEx, 500ms), order books (getQuoteLevel2, 1s
— QUIK returns L2 only while a depth window for that instrument is OPEN in the terminal),
the anonymized all-trades tape (OnAllTrade, 300ms batches — the QUIK "Таблица всех сделок"
window must stay open), instrument params (price step/step cost/margin, 60s). The Go
bridge routes md/book/tape/param events into a `quikdde.Provider` overlay (the package
name is historical — Provider is the market-data hub); the runner builds EXACT OHLCV bars
from the tape (`robot_runner/bars.py`, snapshot ticks muted while the tape flows). The
legacy DDE reader starts ONLY with `SHECTORY_ENABLE_DDE=1`; health/heartbeat judge the
FEED freshness, not the DDE server (a hard `Alive()` check with DDE off would raise a
false CRITICAL DDE_DOWN on every start). Books are forwarded to STL by walking
`Provider.LuaBookCodes()` content-fingerprint-gated — the old DDE-sheet walk finds nothing
post-DDE and silently kills the стакан (bit prod). Operator config lives in a sidecar
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
(`frontend/public/agent-status.html`, same page, `?src=&interval=` params) — align
actions only work against the agent's own loopback, not the mirror. Recon attributes
orders/trades to a robot by a TAG the agent stamps into the QUIK order COMMENT (=>
`brokerref`): robot ID for `rr:` orders, `"recon"` for align orders, empty for MANUAL
(operator's own terminal trading). Untagged = manual: shown separately, never
reconciled, never in an align plan (so robot recon and manual trading never conflict;
`manual_offset` is retired). The recon trade-matcher scopes robot fills to the CURRENT
session (MSK-midnight floor): QUIK's acc_trd only holds today, so older fills are
unmatchable by design, not a divergence (`Manual.AccountNet` = the raw WHOLE-account net,
robots included — label it that way). Robots are edited from the GUI: params via
`/api/robot/{id}/params` (local) or `POST /api/v1/quik/robots/{id}/params` (STL);
params_json alone = light SetRobotParams (next bar); a different max_position/schedule =
full spec re-deploy built from the MIRROR echo (paper strictly from the mirror — that
route can never arm/disarm; 409 when the robot is absent from the mirror, never a silent
ignore). Mode flips are ASYMMETRIC by design: ARMING (paper->real) exists ONLY on the
agent (`/api/robot/{id}/mode`, local console), gated on FLAT (position 0 + no
working/in-flight order + status known) + typed robot-ID confirm. DISARMING (real->paper)
is safe and therefore also remote: `POST /api/v1/quik/robots/{id}/to-paper` relays a full
DeployRobot from the mirror echo with `paper=true`, re-checking the same gates (in mirror,
typed id, flat, no working order). Real statistics survive it — the runner resets
realized/fills only on the paper->real transition (`arming` in `robot_runner/host.py`), and
`algo_trades` keeps its rows `mode='real'` forever. There is no STL route that can arm.
Recon never generates a `close_position` step (a position is contextual — it can include
the operator's manual trading); align does `cancel_order` (orphan) + `fix_state` only,
and the Aligner is structurally unable to place an account-net order. On the stand the P&L
badge (and the chart «Результат» via `netOverride`) show the runner's authoritative
`realized_pnl × ₽/point`. On a paper->real ARMING flip the runner RESETS
`realized_pnl` + the fills tail to zero (the paper era is not real money), so a REAL
robot's P&L reflects real trading only; bars/position are kept (the robot is flat at
arming, and its warmup must survive). Reset fires ONLY on the live paper->real
transition — never on a plain restart or params re-deploy — so a long-running real
robot's history is safe.

**Order flow (human orders and robot orders share the tail):** UI → `POST
/api/v1/quik/orders/*` (`trader/api/quik_orders.py`) → limit check (`trader/quik/
limits.py`) → gRPC enqueue (`trader/quik/server.py`) → agent `trade.Manager` re-checks
limits → file-queue `C:\quik-bridge\cmd.jsonl` (prod QUIK has no LuaSocket) →
`shectory_trade.lua` `sendTransaction` → QUIK. Robot orders enter at the Manager via the
runner bridge with `client_id` of the exact form `rr:<robotID>:<seq>:<uuid6hex>`
(`robot_runner/runtime.py`) — FOUR segments; order events are fanned back only to `rr:`
subscribers. Parse the robot ID with the FIRST colon after `rr:` (robot IDs are colon-free
slugs), never LastIndex — a wrong parse silently breaks robot order attribution for every
REAL robot and paper robots hide it. Replies flow back as OrderUpdate/TransReply; STL order
state in `trader/quik/orders.py`, market state in `trader/quik/store.py`.

**Runner execution parity (paid for in real money):** in backtest/paper every bar's order
fills the SAME bar, so the strategy (which re-derives its intent each bar from the FILLED
position) never re-emits. A live LIMIT at `bars[-1].close` can REST → the position never
flips → the same reversal re-emits every bar (8 stacked real orders at max_position=1,
seen live). Hence in `robot_runner`: (a) REAL orders go MARKETABLE — BUY at ask / SELL at
bid from the host-fed freshest quote (10s freshness, fallback to strategy price); paper
path untouched; (b) `host.tick_robot` cancels this robot's working orders before every
new-bar `on_bar`; (c) a cancel the agent cannot honor (unknown client_id after an agent
restart / QUIK day-expiry) is terminated LOCALLY, or the runner book turns phantom;
(d) `RobotStatusReport.recent_fills` carries the FULL persisted 200-tail, and
`realized_pnl` is PRICE POINTS × contracts, NOT rubles (UI converts via
step_cost/price_step). Thin evening books partial-fill a multi-lot marketable order; the
pre-bar cancel drops the tail remainder — position tops up via averaging, by design.
(e) The runner's stdout is a PIPE to the agent, which on the RU-Windows VDS defaulted to
cp1251 STRICT: a '→' in a FILL log line raised UnicodeEncodeError inside the fill path
and KILLED the runner before persist on EVERY real fill (book froze, strategy re-emitted
all day, 2026-07-13). main.py reconfigures stdio to UTF-8 and event()/consume_events are
try/except-guarded — logging must NEVER sit unprotected in the trade path, and non-ASCII
in hot-path console lines is a loaded gun. (f) Signed-space PARTIAL REDUCE keeps the avg
(fewer contracts, same entry average) — the old else-branch reset avg to the closing
fill's price and mis-realized every later close; fixed IDENTICALLY in
`robot_runner/runtime.py` and `trader/lab/runtime.py` (live/backtest parity — keep them
in lockstep).

**Dual trading safety.** A QUIK order requires the master flag ON in BOTH STL
(`quik_trading_enabled` env) AND the agent's own `agent_config.json`. STL never pushes the
master flag; it pushes only the whitelist + numeric caps via `SetLimits` on connect (so the
two whitelists cannot silently diverge), and the agent echoes its effective limits back via
`LimitsState`. A rejected order text "Торговля QUIK отключена" therefore means the AGENT's
local flag is off. Numeric caps: effective = min(agent_config backstop, STL push), and the
agent only TIGHTENS a push live (`Guard.ApplyPushed` is ceiling-only) — it re-reads WIDER
caps ONLY at start, from `agent_config.json`, which is VDS-side and operator-only. To raise
caps: STL env (`QUIK_MAX_CONTRACTS_PER_ORDER`/`QUIK_MAX_WORKING_CONTRACTS`) + restart STL
FIRST, THEN restart the agent (wrong order leaves the old tight caps in force; bit live).
A wider push that the agent ignores STILL bumps `last_push_unix_ms`, so judge "applied" by
the numbers in `LimitsState`, never by the push timestamp (30.07.2026: env 34/70 pushed,
effective stayed 18/20, then 20/20 = the file's own backstop until the operator edited it).
Chain sizing sanity: the whole-book exit is ONE order, and a reversal holds exit + fresh
entry in flight, so `max_position ≤ per-order cap` AND `max_position + qty ≤ working cap`.
Exceed either and the robot can OPEN via averaging but never CLOSE (bit live 21.07.2026).
Phantom orders QUIK never acknowledges are reconciled to
terminal on BOTH sides (STL `OrderStore.reconcile_pending`, agent
`Manager.reconcileStalePending`, ~20s) so they cannot occupy the working-contracts budget
forever. The agent does NOT persist its own working-order table across restarts: orders
placed before an agent restart can be neither listed nor cancelled by it (kill-switch
included) — QUIK day-expiry clears them at session end. The self-update .bat taskkills
`robot-runner.exe` before copying (an orphaned runner once kept trading against a dead
pipe AND its open exe handle could ship the OLD runner as the "new" one); for a manual
restart still taskkill BOTH exes first.

**Restart/failure immunity (built after real incidents, keep it intact):** closed bars
(600-tail/robot) persist in `runner_state.json` and re-seed a fresh host, so a
long-lookback robot is combat-ready right after any restart; `last_bar_run` seeds to the
restored newest bar so a historical bar is never re-executed against live orders.
Journal auto-heal (`internal/runner/journalsync.go`, 60s): a robot-tagged QUIK trade
missing from the runner's believed book is synthesized back through the normal event path
— idempotent via client_id `rr:<robot>:qsync:<order>:<quikTotal>` + the runner's per-cid
dedup; guards: fresh heartbeat only, 90s trade age (normal path first), working orders
untouched, paper/manual/`recon` tags skipped, 200-tail-cut skip. QUIK fact > agent
belief. vdsguard (`internal/vdsguard/`): pong-silence watchdog — SLOW alerts, HUNG
(>quik_guard_hung_sec, default 300) = CRITICAL alert + forced `info.exe` restart from the
pong-reported QUIK folder (cooldown 900s; never restarts a QUIK that has not ponged this
session or whose folder is unknown), plus RAM health (VDS_LOW_MEMORY <400MB avail or
>=92% load). STRICT operator workflow: before ANY manual Lua/terminal servicing set
`quik_guard_disabled: true` (or stop the agent) — else the guard restarts QUIK
mid-servicing. The agent also self-registers the Windows logon task ShectoryTradeStack
(writes `start_all.bat`: QUIK with `/D` working dir — without it QUIK resolves its crypto
provider against system32 — then 25s, then agent; gate `autostart_disabled`). Delivery
rule: files reach the VDS THROUGH the agent (release zip / self-registration), the
operator never downloads by hand. Still manual after a reboot: Windows auto-logon and the
QUIK key password (Finam build, key-based auth).

**Broker abstraction** (`trader/broker/`): robots trade through one `BrokerInterface`
(`base.py`); the concrete adapter is chosen from `settings.exchange_interface` by
`registry.get_broker()`, which hard-gates live trading on `is_trade_ready()` (all CORE
capabilities present). `FinamBroker` and `QuikBroker` each declare their capabilities
honestly.

**Lab** (`trader/lab/`): paper robots persisted in Postgres table `robots` (the traded
symbol lives in `params_json`, NOT a column); `scheduler.py` runs them, `runtime.py` is the
per-robot execution context. TWO strategy families share the same `STLRuntime` protocol
(both consumed 1:1 by backtest, STL robots AND the agent-side runner): (a) the parametric
REGISTRY in `strategies/library.py` — scriptCode `from ...library import make_on_bar; on_bar
= make_on_bar('<id>')`, all hardwired to M1 (`tf=1` in `make_on_bar`); (b) standalone modules
`strategies/<name>.py` (donchian_breakout, ema_crossover, rsi_mean_reversion, supertrend,
us_open_fvg) that export their own `on_bar`/`on_start`/`on_stop` + a `STRATEGY_META`
(name/source/params_schema) and are registered EXPLICITLY in `list_strategies` (`api/app.py`),
scriptCode `from ...strategies.<name> import on_bar, on_start, on_stop`. Either way a result
row is tagged back to its strategy id on ingest by `_strat_id_from_code` (`api/app.py`) parsing
the scriptCode — a strategy the regex can't match lands untagged. Param ranges in each schema
drive both the Optimizer UI and campaign grids. The backtester + optimizer sweep jobs live
here too (self-healing orphan reaper in `api/app.py`).

`make_on_bar` is the position-management layer every REGISTRY strategy inherits (AVG_PARAMS,
injected by `register`): averaging ladder (`avg_max`/`avg_step_atr`), take-profit `tp_atr`
(×ATR/10), «разножка» `min_gap_pts`, cooldown, betting/SuperAverage — and since 30.07.2026 a
STOP-LOSS `sl_frac`, expressed as a PERCENT OF THE TP DISTANCE (50 = half-way to the take),
0 = off = the historic "averaging instead of a stop". Three rules make it real, keep them:
the stop is checked BEFORE averaging (else the robot tops up a position it should be
leaving), it books as a LOSS for the betting/escalation state, and after it fires the SAME
signal is blocked until it flips or disappears (`sl_block`) — without that the strategy
re-enters on the very next bar and the stop bounds nothing. Standalone modules do NOT get
this layer; each carries its own exits (e.g. `us_open_fvg`: stop from the range edge,
target at `rr_x10` × risk, so widening the stop widens the target — sweep the pair, never
the stop alone).

`allow_long` / `allow_short` (default 1/1 = previous behaviour, injected via AVG_PARAMS
into EVERY registry strategy) are a SWEEP AXIS, not a safety switch: the ability to
short is not free, and on a trending contract one-sided trading can beat two-sided.
A forbidden side means GO FLAT (`want -> 0`), NOT "no signal" — ignoring the signal
would leave a long-only robot sitting in a long against a reversed market with no exit
at all. The gate runs AFTER the `__inv` negation (it filters the side the robot will
actually take). `queue_campaign` pins both axes by default: free, they double the grid
twice over.

WARMUP IS A CORRECTNESS PROPERTY, not a formality. An indicator window barely longer
than its own longest period never diverges, and the signal LOCKS to one sign forever:
`macd_cross` with `slow + signal + 2` and fast=57/slow=48 gave a 60-bar window and the
live robot made 778 long closes and ZERO shorts in six real days while the stand's
console showed «СИГНАЛ ШОРТ» (the console read the full 600-bar tail, the trader read
the 60-bar window). Rule: `4 * max(period…) + …`. `tests/lab/test_signal_both_sides.py`
pins BOTH failure modes across the whole registry — «locked one way» (trades, but the
sign is always the same) and «the window starves the signal» (silent on its own window,
trades on a 4× one) — with a 25-signal sample floor so synthetic noise can't fail it.
Two related traps it also cost us: `fast == slow` makes the MACD line identically zero
and `m > s` then returns -1 FOREVER (8 deployed paper robots sat in a permanent short),
and `rsi_trend`'s default 40/60 thresholds are anti-correlated with its own EMA filter —
one signal in 5800 samples, which is a PARAMETER problem, not a warmup one, so the
logic was left alone.

COUNTER-strategies: any registry id
plus suffix `__inv` (e.g. `macd_cross__inv`) is first-class — `make_on_bar` strips the
suffix and NEGATES the base signal (on some contracts fading the signal is robustly
profitable where following it loses). `queue_campaign.py` and Botstore synthesize the
`__inv` template from the base on the fly. `__inv` is NOT a P&L mirror of the base
(averaging/TP make it asymmetric). Pre-2026-07-25 counter-campaign numbers in the
leaderboard were computed by a LOST uncommitted i9 build and are NOT reproducible —
trust only `camp-20260725-contrredo` and later.

**Market-session oracle** (`trader/market_session.py`): is MOEX FORTS trading RIGHT NOW,
derived from ISS `SYSTIME` (ticks while trading, freezes when closed) — never from a
hardcoded calendar (weekends/holidays have their own sessions; a frozen tape while the
market is CLOSED is normal, while OPEN it is a real QUIK-feed failure). Polled by a
lifespan task onto `app.state.market_session`, served at `GET /api/v1/quik/market-session`,
gates the companion's tape-lag alarm AND the hoster watchdog probe's auto-pause
(`~/stl-watchdog-probe.sh` — lives ON the hoster, not in the repo). `open=None` (ISS
unreachable) is treated protectively (keep alarming).

**Manual smart orders** (`trader/quik/smart_orders.py` = pure engine,
`api/quik_smart_orders.py` = 1s in-process watcher): operator SL/TP/Trail/on-fill/OCO.
Book persists in `data/smart_orders.json`; fired children go through the SAME validated
human place path, client_id `so:<id>` = MANUAL class (robots/recon never touch them).
While STL is down smart orders DO NOT fire — the UI says so explicitly. QUIK expires
unfilled children at session end: the watcher marks those `orphaned` («дочерняя заявка
не дожила») and the UI offers re-arm; never auto-rearm. UI: `components/orders/`
(OrdersFrame tabs: обычная/умные/графики позиций; texts+colors in
`lib/smart-order-help.ts` — one source for the frame, chart lines and legend).

**Robot stand «системный монитор» + LLM companion** (`trader/api/quik_robot_chat.py`,
console in `AgentRobotScreen`): the stand's header is three mini-frames (status / actions /
monitor). The monitor is a green-monochrome console that logs operator COMMANDS, their
replies and STATE TRANSITIONS (mode, pause, exit-only) with a date-time prefix, 1000 lines
in localStorage — and doubles as a chat with a per-robot LLM companion. Hard boundaries,
pinned by `tests/quik/test_robot_chat.py`: the router exposes exactly ONE endpoint and it
mutates NOTHING (read-only by construction, the model has no tools); the persona answers
only about THIS robot; and money is precomputed server-side and handed over as finished
numbers, because the model multiplies points by contracts wrong (it once reported −262k
where the truth was −8.9k). LLM access is Lineman-only (federation policy 18.06.2026):
`POST {LINEMAN_URL}/api/klod/ask`, hoster is inside WireGuard so it dials the proxy
directly, and NO provider key ever lives in this service. Two operational traps: the
`normal` hint routinely 502s over a shared-quota upstream 429, so the code walks a hint
chain (`lineman_model_hint` + `lineman_model_fallbacks`) to the first live one; and Lineman
answers `{"error": "bad JSON"}` to any body over ~32 000 chars — that is a SIZE limit, not
a parse error, so `build_prompt` trims to `PROMPT_BUDGET` by priority (history first, then
docs, then the trade tail) and never touches the persona at the head or the question at the
tail.

**Strategy time semantics (nearly cost real money):** backtest/ISS bars are MSK-wall
stamped as UTC; the AGENT RUNNER builds TRUE-UTC bars from the QUIK tape. A wall-clock
strategy (us_open_fvg's `_hm_day`) must take `bar_offset_min` in params: 0 for
backtest/STL (default, historic behaviour), 180 on an agent deploy — without it the
"16:30 MSK" anchor lands at 19:30 and the 23:45 EOD flatten at 02:45. Deliberately NOT in
params_schema (infrastructure, never a sweep axis). The runner resolves BOTH strategy
families via `host.resolve_on_bar` (registry first, else module import; standalone
modules are bundled by build.spec's collect_submodules). US-open reminder: 09:30 New York
= 16:30 MSK only under US DST (~Mar–Nov); switch live robots' `open_hour` to 17 in early
November and back in March.

**Param sweeps (heavy compute runs on the i9, never the VDS):** queue with
`scripts/queue_campaign.py` from a dev box (`--strategies fvg --symbols RI --date-from …
--include-avg-params --pin qty=1`; `--pin` frees a grid axis — qty only scales P&L). Jobs
land in `backtest_runs` (engine=remote); the pull agent on the i9 box "Win10-HyperV"
(repo copy at `C:\Users\admin\Documents\@FIN\Shectory Trade & Lab`, NO git — sync
`trader/` by hand or via the agent self-update manifest) claims via
`/api/v1/agent/claim`. GOTCHAS: `agent_control.pause_remote='1'` makes claim return 204
forever (agent idles "waiting for jobs"); `shectory-optimizer.service` on the hoster
enqueues its own rounds and competes for the i9 — stop it for a focused sweep; results
from `camp-`/`opt-` prefixed run_ids mirror into `optimization_leaderboard`
(Botstore), bare-cuid runs land ONLY in `backtest_results`; explicit `paramSets` (list of
dicts, e.g. a random no-repeat sample) bypasses the grid product and the local combo cap
on engine=remote. The VDS fallback sweeper only takes jobs ≤150 combos.

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

- **SAFE deploy (default; `deploy/deploy.sh`'s remote `npm build` once thrashed the VDS
  into an operator hard-reboot):** build the frontend LOCALLY, then
  `git push` → `ssh hoster 'cd ~/apps/shectory-trader && git pull'` → **`bash
  deploy/deploy_dist.sh`** — it ships index.html + exactly the assets index.html
  references and then re-reads them THROUGH nginx, failing loudly on a 404. Never
  hand-pick asset files: a deploy that ships index.html+JS but forgets the new hashed
  CSS leaves the site unstyled ("сайт лежит") — happened twice, 23-24.07.2026.
  Frontend-only = NO service restart (nginx serves dist). Backend change = `sudo systemctl restart shectory-trader` (drops the agent
  gRPC link briefly — agent redials; never restart while the operator live-tests trading).
  Assets are hashed + served `Cache-Control: immutable` 1y, so only the FIRST visit pays
  download cost; scp never cleans old hashes so `frontend/dist/assets/` accumulates dozens of
  stale bundles (harmless). If a cold first-load is slow, check nginx `gzip_types` is
  UNcommented in `/etc/nginx/nginx.conf` — with it off nginx gzips only `text/html`, shipping
  the JS/CSS raw (~727KB vs ~218KB gzipped over the thin VDS uplink; this bit prod once).
- **QUIK agent + runner:** scp changed Go files + protos into hoster `~/quik_build`, then
  `bash ~/quik_build/publish_quik_agent.sh [agent_id]` — builds the agent exe, packs
  `robot-runner.exe` into the SAME zip when staged in `~/quik_build/quik_agent/dist/`
  (build it on Windows first, scp it up); the agent self-updates on start/03:00/command
  and its apply-.bat installs companion exes too. `build_rev` must be a NUMERIC epoch.
  Omit `[agent_id]` to publish WITHOUT triggering — the agent applies it at 03:00 (no
  extra live-robot restart). **VERIFY `sha256sum` of the staged runner exe against the
  local build BEFORE publishing, and the zip entry after** (python3 zipfile; no unzip on
  the hoster): a Bash-tool timeout once killed an scp mid-transfer, the staged exe kept
  the right SIZE with wrong bytes, three publishes shipped it, and the runner crash-looped
  on the VDS with `[PYI-x:ERROR] Failed to extract ... decompression -3` (PyInstaller
  archive corrupt; runner stderr is not remoted — diagnosis needed the operator's console).
- **AI46:** `deploy/shectory-ai46.service`; runs `python -m trader.lab.ai46`.
- ssh aliases: `hoster` (prod), `smain` (federation/keymaster). No SSH to the QUIK VDS —
  anything there (QUIK settings, Lua script file, agent_config.json) is operator-only.

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

<!-- onboarding-marker BEGIN — do not edit; rewritten by /onboarding -->
> **Federation onboarding** — канон: [.onboarding/CANONICAL.md](.onboarding/CANONICAL.md); карточка агента: [.onboarding/AGENT.md](.onboarding/AGENT.md).
> При старте сессии Claude обязан запустить `bash ~/.claude/skills/onboarding/bin/check_freshness.sh`. Если `status=stale` или `status=absent` — предложить пользователю `/onboarding` (без долгих объяснений, одна строка).
<!-- onboarding-marker END -->
