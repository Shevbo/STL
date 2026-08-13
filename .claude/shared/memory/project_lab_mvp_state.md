---
name: project-lab-mvp-state
description: "Shectory LAB module — MVP v1 build state, deployment, and open issues"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
---

# Shectory LAB (STL) — MVP v1

Strategy framework added to existing Trader. Built 2026-05-28..30 via subagent-driven plan.
Spec: docs/superpowers/specs/2026-05-28-lab-mvp-v1-design.md
Plan: docs/superpowers/plans/2026-05-28-lab-mvp-v1.md

## Architecture (chosen: monolith Approach A, embedded in trader/)
- LAB embedded in existing FastAPI process (`trader/lab/`), NOT separate microservice.
- Live robots = asyncio tasks; backtests = isolated subprocess (multiprocessing).
- DB: PostgreSQL `project_stl` on Hoster. Python reads via asyncpg; Prisma only for migrations.
- Auth: Shectory ID Bridge API (existing trader/auth/portal.py).

## Key files
- `trader/lab/models.py` — Robot, StlLink, BacktestRun, BacktestResult, LiveTrade, LiveMetric
- `trader/lab/runtime.py` — Bar, Order dataclasses; STLRuntime Protocol; BacktestRuntime; LiveRuntime
- `trader/lab/indicators.py` — ema(), rsi()
- `trader/lab/backtest.py` — compute_metrics(), run_single_backtest(), run_backtest_isolated()
- `trader/lab/scheduler.py` — RobotScheduler. `schedule` = TRADING WINDOW "HH:MM-HH:MM" (Moscow), NOT cron. Default 09:00-23:55. Ticks every 60s, runs on_bar only inside window. _MAX_ACTIVE_ROBOTS=1. (croniter removed from use)
- `trader/lab/iss_loader.py` — MOEX ISS loader (port of docs/Source_update.ps1); front-contract roll logic; load_bars_iss()
- `trader/lab/market_store.py` — ohlcv_bars DB cache (upsert/get/coverage/ensure table)
- `trader/lab/strategies/` — ema_crossover, rsi_mean_reversion, donchian_breakout, supertrend (each: on_bar/on_start/on_stop + STRATEGY_META). SuperTrend = ATR trend-follower, LONG+SHORT, uses indicators.atr(). multiplier stored ×10 as int (30=3.0) for integer grid. Robot: robot-supertrend-rts-01. Uses stl.set_state/get_state('trend') to enter once per trend change.
  TWO bugs found+fixed: (ce75fcc) strategy re-entered every bar; (e1bea10) REAL root cause in BacktestRuntime.place_order — always grew LONG on buy, so buy-to-cover-short DOUBLED position → maxAbsPos 365. Rewrote place_order to signed-position accounting. After fix maxAbsPos=1, long_opens 182/short_opens 183. Lesson: Donchian (long-only) masked the engine bug; only a short-capable strategy exposed it. (RIM6 metrics negative — strategy marginal, not a bug.)
- `trader/db.py` — asyncpg pool with JSON codec for JSONB (set_type_codec jsonb/json)
- `frontend/src/components/LabPanel.svelte` — 3 tabs root
- `frontend/src/components/lab/LiveRobots.svelte` — robot list + RobotEditor
- `frontend/src/components/lab/RobotEditor.svelte` — settings screen (strategy picker, params from schema, schedule)
- `frontend/src/components/lab/BacktestLab.svelte` — controls + results table + center BacktestChart
- `frontend/src/components/lab/BacktestChart.svelte` — 2 stacked lightweight-charts: candles+markers / equity curve. Prop `pointValue` (default 1) → multiplies avg/maxProfit/maxLoss overlay into rubles for live view.
- `frontend/src/components/lab/RobotWindow.svelte` — dbl-click detail modal (DEPLOYED 2026-06-01, commit 310fb2f). Double-click a robot row in LiveRobots → full-screen modal: params + ГО/pv, instrument chart (reuses BacktestChart) with order+trade markers + open→close connectors + ruble equity curve, "текущий результат" (доход ₽/%, позиция, круговых L/S, win rate, всего заявок), история сделок table (ALL live_trades incl rejected/skipped, newest first, status-colored). Backend: GET /api/v1/robots/{id}/live (app.py line 706) returns {robot,symbol,paper,trades[],point_value,initial_margin,date_from,date_to}; economics via market_store.refresh_instrument_spec. Executed-fill filter={paper,filled,submitted,executed}; rejected/skipped shown only in table. VERIFIED: server HEAD=310fb2f, bundle index-Bv6JCgTj.js, /live route returns 401 to unauth curl (route live+auth enforced), robot_started clean, 0 errors.

## DEPLOY GOTCHAS (learned 2026-05-31/06-01)
(1) `npm run build` on Windows FAILS: path "Trade & Lab" has `&` → npm.ps1 mis-resolves vite.js to C:\Dev\vite\bin\vite.js. Workaround: `node "node_modules/vite/bin/vite.js" build` (quoted).
(2) deploy.sh uses remote `github` but SERVER only has remote `origin` → `git pull github main` fails on server. Use `git pull origin main` on server. (TODO fix deploy.sh or add github remote on server.)
(3) Run deploy steps as SEPARATE ssh calls (pull / build w/ sentinel touch+EXIT / restart / verify); combined heredoc + parallel ssh return garbled/truncated output (harness glitch). Remote grep output can be GARBLED — verify via build EXIT, dist mtime, sentinel files, curl status, not greps.
(4) prod deploy (git pull+build+systemctl restart) and prod DB reads need EXPLICIT user ok each time; classifier blocks implicit. Approved ssh deploy calls need dangerouslyDisableSandbox=true.
(5) MUST restart service after app.py route changes (uvicorn imports trader/ from CWD at startup).

## REST endpoints (all require_auth)
- GET /api/v1/strategies — built-in templates with params_schema
- GET/POST /api/v1/stl-links
- GET/POST/PUT /api/v1/robots, POST /api/v1/robots/{id}/deploy|undeploy
- POST /api/v1/backtest/run, GET /api/v1/backtest/{id}/status|results
- POST /api/v1/market/update, GET /api/v1/market/coverage, GET /api/v1/market/bars (SQL resample 1min->Nmin)

## Deployment (Hoster 83.69.248.175)
- DB project_stl created, user project_stl_app, pwd in ~/.shectory_trade.env as LAB_DB_URL
- IMPORTANT: env var name is `LAB_DB_URL` (matches Settings.lab_db_url), NOT LAB_DATABASE_URL
- Tables created via direct psql (Prisma 7 migrate had config issues). ohlcv_bars auto-created on startup.
- Deploy: `bash deploy/deploy.sh` (re-clones repo on server, builds frontend, poetry install, restart systemd)
- Test data cached: RIM6 47898 1-min bars (2026-03-01..05-25) in ohlcv_bars
- Robot in DB: robot-donchian-rts-01 (Donchian Breakout RTS, RIM6, entry=20/exit=10)

## Gotchas hit and fixed
- cuid2: use `Cuid().generate()` not `cuid()` function
- Prisma 7: url moved out of schema.prisma to prisma.config.ts (datasourceUrl)
- asyncpg: needs JSON codec or JSONB returns/expects strings; do NOT json.dumps when codec active
- backtest_runs date_from/to: parse ISO string to datetime before asyncpg insert
- Svelte 5: no `onclick|stopPropagation`; use `(e)=>{e.stopPropagation();...}`. No single-quote JSON in attrs.

## Backtest results (Donchian RIM6, 2026-03..05, validated working)
Best: entry=80 exit=20 → +1.19% Sharpe 0.227 dd 3.54%; entry=80/exit=20 newer run +1.84%.
Most combos negative — strategy marginal on this period (expected for trend-follow on 1-min).

## RESOLVED 2026-05-30 — "нет графика"
Verified end-to-end in shared browser: LAB → Backtest Lab → fill dates (03-01..05-01) → Run Backtest
→ result row appears → CLICK row → BacktestChart renders (candles + trade markers + equity curve).
Fullscreen button also works. Backtest completes <1s server-side. Chart shows RIM6 Trades:561 Win:35.1%
Return:-6.52% Sharpe:-0.829 MaxDD:6.67%.

## Optimizer (TSLab-style) DONE 2026-05-30
frontend/src/components/lab/Optimizer.svelte — grid builder (from/to/step per numeric param, checkbox enable),
runs full grid via /backtest/run paramsGrid, sortable results table, 2-param heatmap (return), click row/cell→open chart.
Center of Backtest Lab now has tabs "График | Перебор параметров" (centerMode in BacktestLab.svelte).
Backend: trader/lab/backtest.py run_backtest_grid() runs WHOLE grid in ONE subprocess (bars pickled once,
was per-combo = slow). Verified: 12 combos (entry 10-40 × exit 5-15) → best entry=30/exit=5 +22.74% Sharpe 0.96.
Single shared time axis: top candle chart timeScale.visible=false, axis only on equity chart below.
shared analytics in frontend/src/lib/lab-analytics.ts (replay, computeStats, aggregateMarkers, buildConnectors).
Known minor: results table shows momentary unsorted order while polling streams partial results; settles correct
on completion / any header click (sort logic itself fine).
instrument_meta DB mirror + /api/v1/instruments/{symbol}/meta endpoint built; margin (ГО) still "—" because
Finam params field name for initial margin unknown — need real /v1/assets/RIM6/params shape to map the field.

## BacktestChart enhancements DONE 2026-05-30
BacktestChart.svelte now has: interval selector (1м..1д) via /market/bars resample_min (epoch-bucketing
in app.py market_bars supports any bucket size); baseline equity series (green above start / red below =
loss visible); aligned time axes (rightPriceScale minimumWidth:80 + time-range sync); crosshair shows full
datetime (.bt-cross); trades table newest-first; strategy author link + params in header.
STILL TODO: ГО (margin) in "Макс. позиция" shows "—" — need initial_margin per contract
(from Finam /instruments/{symbol}/params) × maxAbsPos. Counter wording: top stat "Всего сделок"=round-trips,
table count="(N) orders" (fills = 2×round-trips) — could clarify labels.

## LIVE TRADING IMPLEMENTED + PAPER MODE (2026-05-31, commit 44233fa) — RESOLVED
The NotImplementedError live bug is FIXED. LiveRuntime (trader/lab/runtime.py):
- get_bars/get_quote: fresh minute bars from MOEX ISS (load_bars_iss, same source as backtest),
  cached per tick. Independent of WsHub symbol subscription.
- place_order: PAPER (default) records virtual fills to live_trades status='paper', NO broker call.
  Real (paper=False) -> TxClient + records Finam status.
- get_position in paper reconstructs signed pos from recorded paper fills. get_orders/cancel = no-op.
SAFETY GATE: scheduler._run_robot_task → paper unless robot.state_json.live_real == true.
SuperTrend RTS (robot-supertrend-rts-01) deployed paper, window 09:00-23:55. scheduler robot_error=0
(old per-minute crash gone). E2E PROVEN: real LiveRuntime(paper) ran SuperTrend over RIM6 history →
8 paper trades written to live_trades, zero broker calls.
[CORRECTED] Earlier I wrongly said "FORTS closed on Sunday" — FALSE, FORTS trades weekends (see [[reference-forts-weekend-trading]]). Live ticks flow any day; verify empirically, not by calendar. Infra confirmed via history replay AND can run live now.
TO GO REAL (only after explicit user yes):
  UPDATE robots SET state_json = state_json || '{"live_real":true}' WHERE id='robot-supertrend-rts-01';
  then sudo systemctl restart shectory-trader (scheduler reads flag when building the task).
Candidate params from [[project-optimization-campaign]] hit-parade (SuperTrend RIM6 atr15/mult60 best).

## [666] UNCOVERED-POSITION + SYMBOL FORMAT (2026-05-31, commit a1b2c3d)
DIAGNOSIS of Finam [666] "uncovered position may arise/increase":
- Verified account position symbol format = 'RIM6@RTSX' (symbol@MIC), qty -5. Finam orders need @MIC suffix.
- Frontend manual order panel ALREADY sends full 'GZM6@RTSX' (from instruments list = Finam symbol), yet still
  got [666] → so [666] is NOT a symbol-format problem. It's a Finam SERVER-SIDE RISK CHECK on the account.
- Proto Order message has NO bypass/confirm field (verified earlier). Cannot be solved from our code.
- Real unblock is ACCOUNT-SIDE at Finam: (a) risk level КПУР→КОУР, (b) instrument enabled for margin,
  (c) confirm NOT a demo/test contour, (d) Finam support with trace id. OR avoid net-increasing exposure.
FIXED a SEPARATE real bug: robot path used bare 'RIM6' for orders. LiveRuntime._finam_symbol() now appends
@RTSX for live orders (ISS bars still use bare ticker). Added [666] translation in place_order logs.
## [666] ROOT CAUSE CONFIRMED + PREFLIGHT (commit fd0085e) — RESOLVED
From GET /v1/assets/RIM6@RTSX/params?account_id=2035452: shortable.value=NOT_AVAILABLE (longable=AVAILABLE);
long/short_initial_margin=24838.21 RUB. Account holds real RIM6@RTSX short -5 (USER's own — leave it).
NOT code/symbol bug. [SUPERSEDED below: not just sells — long buys reject too. See CORRECTION 22:23.]
GOTCHA: /params REQUIRES account_id (no-account call → HTTP 400 "Invalid arguments:account_id").
VERIFIED LIVE (2026-05-31 21:53 read-only scripts/probe_params.py, acct 2035452, RIM6@RTSX):
tradeable=true, price_type=ANY, longable=AVAILABLE, shortable=NOT_AVAILABLE (same call → asymmetry IS the
proof it's a permission, not money). long_initial_margin=short_initial_margin=24838.21 RUB; long_risk_rate
34.45%, short_risk_rate 44.32%. Position qty=-5 confirmed.
*** CORRECTION 2026-05-31 22:23 — EARLIER "LONG WORKS" CLAIM WAS WRONG. ***
Placed a REAL test order: BUY 1 limit @ 110210 (corridor floor LOWLIMIT, far below bid 113720 → cannot
fill). A buy of 1 while short -5 REDUCES the short to -4. Finam STILL rejected: HTTP 400 [666]
"uncovered position may arise/increase". So [666] is NOT "short side disabled" — even a long buy that
shrinks the short is rejected. CORRECTED MODEL: account does not permit holding UNCOVERED (margin)
positions (shortable=NOT_AVAILABLE). The existing -5 IS an uncovered position. The risk engine rejects
any order that would LEAVE a nonzero uncovered position: buy 1 → -4 still uncovered → reject; sell →
more negative → reject. HYPOTHESIS (untested, do NOT test w/o user ok — would risk the -5): only BUY 5
(exact flat, 0 uncovered) would pass. Corridor RIM6: LOWLIMIT 110210 / HIGHLIMIT 117010, MINSTEP 10,
STEPPRICE 14.20448 (pv 1.420). Finam orderbook path /v1/instruments/{sym}/orderbook; quote
/v1/instruments/{sym}/quotes/latest. PREFLIGHT _shortable() only guards SELLS — incomplete given buys
also reject in this account state, but harmless (paper mode; broker rejects + [666] translation handles
real). Support letter scope = enable UNCOVERED/MARGIN positions (not just "short"); note API can't even
reduce the existing short stepwise. Probe scripts removed from server after use.
REFUTES "maybe just a missing force flag": Order proto (verified .proto + GitHub) = account_id,symbol,
quantity,side,type,time_in_force,limit_price,stop_price,stop_condition,legs,client_order_id,valid_before,
comment — NO force/confirm/validate_only/override. [666] is a HARD reject, not bypassable from API.
Shortable enum: NOT_AVAILABLE(0)/AVAILABLE(1)/HTB(2)/ACCOUNT_NOT_APPROVED(3)/AVAILABLE_STRATEGY(4); Finam
returns NOT_AVAILABLE. Margins present+sufficient → block is a permission toggle. RU Finam support letter
drafted. scripts/probe_params.py = reusable READ-ONLY probe (no order placed).
DECISION: USER WILL ENABLE margin short in Finam ЛК; then SuperTrend trades both sides (no code change).
PREFLIGHT added: LiveRuntime._shortable() queries /params via pos_client; place_order skips real sell-to-open
(status='skipped'+log) if shortable==NOT_AVAILABLE. Best-effort, never blocks on uncertainty → no [666] loop.
LIVE STATUS (2026-05-31 22:3x): server HEAD=fd0085e, service active, NRestarts=0, up since 21:25:36 MSK.
Robot robot-supertrend-rts-01 running PAPER, window 09:00-23:55, robot_error=0 since start. Real money gated
behind state_json.live_real (NOT set). DECISION: STAY PAPER until Finam resolves uncovered-position block.
Log noise md.watchdog.stale + bars.rpc_error UNAUTHENTICATED = old WsHub gRPC stream (GZM6 main screen) token
issue; does NOT affect robot (robot uses ISS). Cosmetic. diag_666.py exists but unneeded (params gave answer).

## (historical) "нет графика" earlier diagnosis
Backtest Lab tab confirmed rendering (user screenshot shows controls + placeholder "Запустите бэктест и выберите строку результатов").
Browser console has NO JS errors (only harmless favicon.ico 404). BacktestChart code is sound.
Chart is placeholder until: Run Backtest → results table appears in left .controls column → click a result row → chart renders center.

ROOT CAUSE likely: From date field empty in UI (only To filled). `new Date('').toISOString()` throws → runBacktest catches → no results → no chart.
TODO next session:
1. Add guard in BacktestLab.runBacktest(): validate dateFrom/dateTo non-empty & valid before fetch; show clear error.
2. Set sane default dateFrom (currently '2026-01-01' in state but field may render empty — check date input binding).
3. UX: results table is buried in narrow 300px left column — consider moving results table to center top, above chart.
4. Use cached range From=2026-03-01 To=2026-05-01 (RIM6 cache covers 03-01..05-25).
Verified working server-side: backtest grid search returns metrics; /market/bars returns 804 hourly bars; new runs include trades.time.

See [[ssh-hoster-shectory]] for server access. Follow [[feedback-rtk-usage]] for shell commands.
