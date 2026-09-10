# Architecture — the parts that span files

Moved out of `CLAUDE.md` on 07.09.2026: 28 KB / ~8 300 tokens that were riding in every
request. This file is indexed — reach it with `rag_search`, do not paste it back.

Здесь описано, ПОЧЕМУ платформа устроена так, а не где лежит конкретная функция. Отвечает
на вопросы: почему живые роботы крутятся на QUIK-агенте, а не внутри STL; какие два gRPC
контракта связывают агента с сервером и почему агент звонит наружу сам; полный путь заявки
от кнопки в интерфейсе до биржи и обратно; чем исполнение робота в бэктесте отличается от
живого и почему паритет стоил реальных денег; как устроена двойная защита от случайной
торговли; что переживает рестарт агента и сервера; зачем брокерская абстракция; как
собран Lab и бумажные роботы; откуда платформа знает, торгует ли сейчас FORTS; как
работают умные заявки и стенд робота; почему бары стратегии живут по московскому времени;
почему перебор параметров уезжает на i9 и никогда не идёт на VDS; и почему никакие
вычисления не выполняются внутри процесса API.

Синонимы, которыми это называют: архитектура, устройство платформы, как оно связано, что
с чем разговаривает, контракты, схема работы, why is it built this way.

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
from the tape (`robot_runner/bars.py`, snapshot ticks muted while the tape flows). That
muting EXPIRES after `TAPE_PRIORITY_MS` (30s), so a silent tape falls back to quote
snapshots and builds FLAT minutes out of a frozen price — overnight that is hundreds of
invented candles. `Bar.traded` marks a minute that carried a real tape trade (default
True: backtest/ISS bars are exchange fact; it round-trips through `to_rows`/`seed` as a
7th column, and a legacy 6-column row counts as real). Anything shown to a HUMAN takes
`traded_bars()`; the STRATEGY keeps reading `bars()` unchanged — filter the TAIL, never
the builder, or you are editing trading, not the display. The
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

Two more entry gates live in that same layer, both default-OFF and both gating ONLY
entries/adds — never an exit:
- **«Долина смерти»** (`dv_bars` + `dv_range_pts`, both > 0): the range of the last
  `dv_bars` CLOSES is narrower than `dv_range_pts` points = a sideways corridor, where a
  trend signal saws crossovers and pays it out in commission. By CLOSE, not high/low: a
  market-maker wick must not cancel the valley. In the valley the robot opens nothing,
  reverses into nothing and adds nothing — but the signal is still computed on the RAW
  series. Freezing the series was tried twice and BOTH ways bit: cutting all valley bars
  starved the warmup and left the robot mute for hours AFTER a 600-point breakout
  (07.08.2026); cutting only the trailing valley froze the signal inside it and thereby
  took away the robot's signal EXIT — a short sat 290 points offside with no stop
  (08.08.2026). The filter gates orders, never the maths.
- **Разножка in ATR units** (`min_gap_atr`, ×10; the fixed-point `min_gap_pts` and the
  amplitude-based `gap_auto` still work — the EFFECTIVE gap is the MAXIMUM of whatever is
  enabled, since a weaker limiter must not cancel a stronger one). Why it exists: the
  averaging STEP is measured from the AVERAGE, and every add drags the average toward
  price, so each next add needs less real movement and the ladder collapses — live lxk22
  on 10.08.2026 walked 120, 40, 80 and 30 points and bought 11 of its 17 contracts at ONE
  price. Разножка measures from the last FILLED add, and in ATR units it also scales
  itself: wide in a rally, tight in a quiet market, never zero.

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
BOTH TRAPS REPEAT PER AXIS NAME, and the 05.08 sweep missed `shectory_2ema` on both
counts (fixed 09.08): `ema1 == ema2` is the same degeneracy as `fast == slow`, but
`valid_macd_config` only ever read the fast/slow keys, so every MXU6 leader in the
leaderboard is an ema1==ema2 row with `max_mae` EXACTLY 0 and RF ~50 000, and five
paper robots (SVU6/RIU6/NGQ6/MXU6/GDU6) sat in a permanent short. Its warmup was
still `ema2 + 2`: on the SAME bars the GDU6 leader prints +160 688 with a 281-bar
window and −125 288 with the honest 1116-bar one — the sign flips on warmup alone.
The registry test only exercised DEFAULT params, and degeneracy lives at the grid
EDGES, so it now runs the equal-period case for every fast/slow and ema1/ema2 pair.
When adding a crossover strategy, add its period pair to `_EQUAL_PERIOD_AXES` and to
`valid_macd_config`, or the next campaign re-queues the same mirage. A THIRD trap of
the same family: the i9 ran a stale copy on 05.08, so leaderboard rows with
`allow_long`/`allow_short` are byte-identical across 0/1, 1/0 and 1/1 — a pull of the
same three configs gives 154k/105k/259k. Verify a row before trusting it.

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
Kind ids on the wire stay `sl`/`tp`/`trail_tp`/`on_fill`, but the OPERATOR-FACING names
were changed 09.08.2026 to «Условная» / «Лимитная» / «Следящая» / «Зависимая» — rename
texts in `lib/smart-order-help.ts`, never the ids. `sl_offset`/`tp_offset` («блоки после
сделки») are allowed on EVERY kind since the same day: a smart order only ENTERS and
forgets the position afterwards, so without that pair there is nothing to exit with. No
recursion is possible by construction — `_protective` gives its children no offsets.
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
chain (`lineman_model_hint` + `lineman_model_fallbacks`) to the first live one — but that
chain is only as long as the ENV makes it: `LINEMAN_MODEL_FALLBACKS` was simply unset on
the hoster, so the "chain" was the single hint that 429s, and the robot chat stayed dead
while the proxy itself was healthy (10.08.2026; a 401 there means expired federation
OAuth, a 429 means shared quota — different diagnoses). Judge the channel by walking the
hints by hand, not by the first one; and Lineman
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
`/api/v1/agent/claim`. **A NEW PARAM AXIS MUST BE PROBED BEFORE IT IS SWEPT**: the i9
holds its OWN copy of `library.py` and refreshes it only on `agent_control.update_token`,
so an unknown key is silently ignored and EVERY combo returns the same number — which
reads as "the parameter does not matter", not as "the parameter did not run". Queue 2
combos (axis off / axis on hard) at priority 100: ≤ `MANUAL_SIDE_MAX_COMBOS` (32) they go
to the RESERVED worker and do not evict a running campaign. Identical `net` AND
`total_trades` = dead axis → set `update_token`, wait for `lib_sha` in `i9_heartbeat` to
match `sha256sum trader/lab/strategies/library.py | cut -c1-12` on the hoster, re-probe.
Never judge the i9's code by the heartbeat's `version` — that string is a constant in the
agent, independent of the library. Both outcomes happened on 10.08.2026 within one hour
(`dv_bars` alive, `min_gap_atr` dead). GOTCHAS: `agent_control.pause_remote='1'` makes claim return 204
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


## Frontend and Companion — screen mechanics

Moved out of `CLAUDE.md`'s Overview on 07.09.2026 — implementation detail, not the summary
a project overview needs.

**Frontend robot stand.** `AgentRobotScreen` is the stand, `RobotWindow` is a 50-line modal
wrapper around it, and the SOURCE is chosen SERVER-side by `GET /api/v1/lab/robot-stand/{id}`
(agent robot from the mirror, STL paper robot assembled by `trader/lab/robot_stand.py` into
the SAME mirror-shaped record). The response carries `caps` (quik/chat/commands/signal): a
frame the source can't support is NOT RENDERED, never rendered empty. Per-robot showcase:
`/?agent_robot=<robot_id>`, modal `/?lab=live&robot_win=<id>`. Two-level burger nav
(`NavMenu.svelte`) is mounted on EVERY screen; deep links `?orders=1`/`?tables=1`/`?equity=1`
open frames. Panels are `Frame.svelte` (collapse + maximize via a shared `maxId`) split by
`Splitter.svelte`, each size persisted per layout profile. The browser tab title is set from
what is open (`lib/page-title.ts`) — content first, product suffix last, since a narrow tab
shows only the first characters; a full-page screen owns its own title and the shell must
not overwrite it.

**Companion drag and auth.** Bottom-right, frameless always-on-top. Auth: one-time pairing
code (issued on /watchdog-log.html) -> long token, DPAPI-encrypted per Windows account; the
token opens EXACTLY ONE endpoint (`GET /api/v1/quik/companion/snapshot`, read-only) — pinned
by tests in `tests/quik/test_companion.py`. The panel is DRAGGABLE by any free spot (NOT by
a header strip — the panel's content is rebuilt from each snapshot, so a handle bound to one
row eventually disappears with it; a 3px slop threshold keeps plain clicks native). That one
feature spans both halves: the page sends incremental mouse deltas to loopback `/move`, the
exe moves the window and remembers the spot in `config.json` (`placed`/`pos_x`/`pos_y`, tray
item «Вернуть в правый нижний угол» resets it). A frameless WebView2 window CANNOT use the
system title-bar drag — the mouse belongs to the WebView2 child window, so WM_NCHITTEST
never reaches us. Deltas are scaled by `devicePixelRatio` (the page measures CSS px, the
window lives in physical px) and the saved origin is always re-clamped to the VIRTUAL screen
(all monitors) so an unplugged monitor cannot hide the panel.
