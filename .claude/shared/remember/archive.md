# Archive

## Week of 2026-08-05
Enhanced ParamPanel UI (sheet, draggable frames, keyboard nav, registration); library.py infra (i9 self-update, dv_filter, nginx, SetPosition refactor). Fixed critical bugs: TP oracle, margin overstatement (2.4×), journalsync QUIK-drift, MACD warmup (60→238), shectory_2ema (ema1==ema2, warmup, queue), dv_sig_window. Deployed frontend drag-drop, mini-chart widget (320×64), runner.exe + Go agent (696 Py/79 Go/168 FE), bar-density UI (30–200), backtest leaderboard (21k rows). RIU6 hourly complete; lxk22 +10k pts; 713 tests.

## Week of 2026-08-03
Fixed serialization bugs (job_body/params_json double/triple-encode, 2,744 runs); repaired 8 robots, scheduler bars. Debugged companion snapshot API perf (21.6s→54ms via missing 3.5M-row index + cache); fixed test-stand auth. Deployed Shectory P&L reconciliation (168,252₽ verified) and cassette UI redesign.

## Week of 2026-07-29
Refactored AgentRobotScreen (3-frame redesign, Lineman agent); fixed position-sizing (16→34 via 2.4×), chart-table mismatch. Fixed 10+ bugs (param panel, filter calc, VM); exit-only mode (soft exits, cross-order alerts), stop-loss (½TP, dd 19.9k→2.2k). Enhanced runner diag, lab-analytics integ, 5 revisions (660 tests). Fixed taker/maker commission (253k₽); Williams %R sweep (4 inst., 88–90%); archive tracker UI.

## Week of 2026-07-21
Deployed Companion.exe (Windows tray, WebView2, DPAPI auth) with live portfolio/robot/watchdog display; rebuilt smart-orders UI (SL/TP/Trail/OCO, 2-click arm, orphan autoheal) and fixed 3 commission bugs. Integrated MOEX ISS oracle for session gating, watchdog SMS; optimized STL cache (8.3→0.012s) and tuned i9 workers. Completed 28-robot rename with live quotes, fixed 8+ logic bugs (phantom VM, SMS gateway, routing), resolved 11.7-load CPU spike, hardened SMS-watchdog with Telegram failover. UI polish: collapsible alerts, position display, header; audited 175 commits, cleaned 72 temp screens + 8 branches.

## Week of 2026-07-14
Armed live trading (Bollinger M1·RIU6, OrderBlock·BRU6) and fixed critical bugs (UTF-8/cp1251, QUIK journal sync, fills recovery). Deployed auto-heal, bar persistence, watchdog (RAM/RTT), restart immunity; cleaned 33 dead-code (5.8k LoC). Added per-robot logging, strategy pages, P&L reconciliation (commission tracking UI); hardened agent control (pause/arming/set-position, phantom recovery). Shipped TP/SL-by-depth backtest UI with per-level metrics; deployed auto-updater; flagged critical issues (SetPosition, v2 divergence, VDS RAM).

## Week of 2026-07-07
Fixed symbol KeyError and DDE watchdog infrastructure bugs. Deployed per-robot event logging and strategy pages. Swept counter-strategies (macd +419k RF 4.27); hardened runner UTF-8 crashes; us_open_fvg live with orphan-guard; backfilled top-3 campaigns.

## Week of 2026-07-06
Swept 100k FVG params (17/21 profitable, macd_cross +670k); deployed param-editor UI, agent panel, backtest-sweep UI, run-history table (sort/filter/12 cols); fixed Lua crash (desync → 6 missed fills), symbol KeyError, DDE watchdog (892→0), UnicodeError, partial-close P&L, i9 queue/zombies. Built showcase layer (campaign-result DB, top-3 ranking); hardened zero-downtime deployment; 54 tests passing.

## Week of 2026-06-29
Fixed robot_runner order re-emit (backtest/paper/real distinction); deployed live dashboard + showcase UI (auth OK). Fixed Lua DDE bypass, orphaned orders snap, P&L calculations. FVG-RIU6 live: +880pts SELL, position limits 3/6 effective. Purged DDE legacy code; queued 234 backtest explorer jobs.

## Week of 2026-06-22
Deployed AI46 backtester (6m/1m OFI proxy, commission-aware, 566→145ms HMM opt). Ran 160-backtest sweep (76 passing, −0.62–1.65% net returns). Phase 7b live (20 tickers, paper trading). Fixed i9 infra (bars cache, dropdown API for 60-contract opt), ollama overload. Chart improvements (zoom, panning). Paused 160-unit sweep pending stabilization.

## Week of 2026-06-15
Completed M6→U6 robot migration (21 robots, pool 12→50) and ported AI46 feature-engine to Go (11 tests). Graphified codebase (4.7k nodes, 18 security/perf findings). Deployed enhanced Showcase (live robots, P&L metrics, trades feed, gRPC Bearer auth). Completed team-46 Ph1-4 (36 tests, 27pg whitepaper, GH token). Queued 420 backtests (557k combos).

## Week of 2026-06-08
Shipped agent control infra (pause/resume/stop/start) and BacktestLab redesign (equity metrics, leaderboard, grid-sweep). Added 3 strategies (FVG/Order Block/Pivot); deployed FVG paper trading (BRN6: RF 3.88, 305 trades). Fixed state amnesia via scheduler persistence, 413/500 errors, i9 KeyError. User feedback drove Russian i18n; optimized VDS; resolved post-deploy issues (self-update, param sync).