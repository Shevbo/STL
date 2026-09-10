# Critical gotchas — STL

Full list of the traps that cost real money or prod downtime. Kept here (not in
`CLAUDE.md`) because they load into every request there; `rag_search` finds this file
when a question touches one of these areas. The five that must be in your head before
you touch anything are reproduced inline in `CLAUDE.md`.

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
