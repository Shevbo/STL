# Archive

## Week of 2026-09-29
Framework implementation (retro_reverse noise-control, tests✓, i9 verified); Interfax news integration (370 articles, 3 dates); yearly impulse framework (2038 events); imp-ri-1 blocked (−1.3 median rollback), imp-ri-2 confirmed volume artifact (−60pt ex-post). Trade tape silent since 2026-09-26; retro-reverse results pending.

## Week of 2026-09-23
Evaluated retro_reverse strategy (noise-control framework); scraped Interfax news (370 articles, 3 dates, MA crosses tested); yearly impulse backtest (2038 events, tests✓); imp-ri-1/imp-ri-2 artifacts identified (post-hoc selection, weekday logic). Trade tape stalled since 09-26; data recovery pending.

## Week of 2026-09-16
Designed & backtested triangular arbitrage (RI×Si vs MX, 1:1.62:2.67, 144 params). Tested cost_atr filter & lxk22 ±1% stop (36d preview: saved 58–77k₽, max DD 163k→107k). Validated BT (fixed extractor, ordered fills); diagnosed ATR weekend reposition bug→atr_skip_weekend patch (24 tests). Implemented flip_back (exit extreme vs loss); avg_from_last position averaging (487/5378 fill-avg bug, fix pending).

## Week of 2026-09-09
Intensive rich_fool optimization (6 waves, d_coef 0.05–1.0, regime-dependent). Resolved 9 formula contradictions, cache defect (7am rare event), overnight session logic. Wave 6 (76.8k combos) deployed with auto d_coef; i9 accelerated to 217 runs/min (1.7x→2x); batch 1 RI complete. OOS validation gate (rf_oos_gate.py) built; rich_fool OOS failed (−96K/−28K, gap-at-open driver); impulse_fade rejected (lookahead artifacts). Valley_spike.py drafted (16–27 trades/Q).

## Week of 2026-09-02
Root causes ID'd: commission 2x overstated (9.9 vs 5.8 ₽/lot); account ВМ excludes manual orders. Daypnl.go/_test.go written; P&L divergence (companion vs main VM) 24h+. Tested vol filter (2022–23: −642k; 2022–26: +366k [95%]); regime-switch protocol (Steps 0–3, v2 all 15 net-negative). Collected BookRuntime/orderbook (2884 fills); fixed us_open_fvg entry; spread 5ppts median.

## Week of 2026-08-19
Deployed shectory-trader 1787379836 (6 fixes: Lua GC 9.1h stable, order cap, fixation alert, mem metrics, hourly guard, journal healing); <1m downtime, 9.3h uptime (+147.6k fin). Archive gzip fixed (JSONL writes correct); recovered corrupt .gz; dup key settings.json resolved. Devmail_hook.py STL_WINDOW fixed; extended 3-window sync (real-trade/backtests/ui-ux). Deployed devchat.html (280 tests); completed trail_sl enhancements; wrote devmail_autopilot watchdog; fixed 403 lineman proxy.

## Week of 2026-08-12
Archive recorder deployed (trader/quik/recorder.py); proto schema refined (TapeTrade/TapeBatch); gzip append bug fixed (recovered 2538 stack/1993 ticks). SMS handler hardened, inbox refresh (POST /inbox/refresh) with dedup. Mail hook optimized (2.3s→100ms). Devmail_hook fixes (STL_WINDOW env, 3-window sync); devmail_autopilot watchdog written; fixed 403 lineman proxy (ANTHROPIC_BASE_URL headers).

## Week of 2026-08-05
Deployed min_gap_atr & inter-window msg API; completed first opt campaign (272 combos RIU6) with k_avg as main driver. Fixed chart coordinates, deployed UI refresh (price scale, time axis, height, curve-switcher, candles). Queued verification (144 combos) and stop-loss sweep (112 combos). UI fixes: companion panel (DPI/monitor), ORDERS frame (type-grouping, gesture controls), robot-card labels, order-xfer settings.

## Week of 2026-07-29
Refactored AgentRobotScreen (3-frame redesign, Lineman agent); fixed position-sizing (16→34 via 2.4×), chart-table mismatch. Exit-only mode (soft exits), stop-loss (½TP, dd 19.9k→2.2k). Enhanced runner diag, lab-analytics integ (660 tests). Fixed taker/maker commission (253k); Williams %R sweep (88–90%); archive tracker UI.

## Week of 2026-07-21
Deployed Companion.exe (Windows tray, WebView2, DPAPI auth) with live portfolio/robot display. Rebuilt smart-orders UI (SL/TP/Trail/OCO, 2-click arm, orphan autoheal); fixed 3 commission bugs. Integrated MOEX ISS oracle, watchdog SMS; optimized STL cache (8.3→0.012s). Completed 28-robot rename, fixed 8+ logic bugs, resolved 11.7-load CPU spike, hardened SMS-watchdog. UI polish: collapsible alerts, position display, header; cleaned 72 temp screens + 8 branches.

## Week of 2026-07-14
Armed live trading (Bollinger M1·RIU6, OrderBlock·BRU6); fixed UTF-8/cp1251 crashes, QUIK journal sync, fills recovery. Auto-heal, bar persistence, watchdog (RAM/RTT), restart immunity. Per-robot logging, strategy pages, P&L reconciliation. Shipped TP/SL-by-depth backtest UI; deployed auto-updater.

## Week of 2026-07-07
Fixed symbol KeyError, DDE watchdog bugs. Per-robot event logging, strategy pages. Swept counter-strategies (macd +419k RF 4.27); hardened UTF-8 crashes; us_open_fvg live with orphan-guard.

## Week of 2026-07-06
Swept 100k FVG params (17/21 profitable, macd_cross +670k RF 4.27); deployed param-editor UI, agent panel, backtest-sweep UI, run-history table (sort/filter); fixed Lua crash, symbol KeyError, DDE watchdog (892→0), UnicodeError, partial-close P&L. Built showcase layer (top-3 ranking).

## Week of 2026-06-29
Fixed robot_runner order re-emit (backtest/paper/real); deployed live dashboard + showcase UI. Fixed Lua DDE bypass, orphaned orders snap, P&L calculations. FVG-RIU6 live: +880pts SELL. Purged DDE legacy; queued 234 backtest jobs.

## Week of 2026-06-22
Deployed AI46 backtester (6m/1m OFI, commission-aware, 566→145ms HMM); 160-backtest sweep (76 passing, −0.62–1.65% returns). Phase 7b live (20 tickers, paper trading). Fixed i9 infra, chart improvements, paused sweep pending stabilization.

## Week of 2026-06-15
Completed M6→U6 robot migration (21 robots, pool 12→50); ported AI46 feature-engine to Go (11 tests). Graphified codebase (4.7k nodes, 18 findings). Deployed Showcase (live robots, P&L metrics); completed team-46 Ph1-4 (36 tests, 27pg); queued 420 backtests (557k combos).

## Week of 2026-06-08
Shipped agent control infra (pause/resume/stop) and BacktestLab redesign (equity metrics, leaderboard, grid-sweep). Added 3 strategies (FVG/Order Block/Pivot); FVG paper (BRN6 RF 3.88, 305 trades). Fixed state amnesia, 413/500 errors, i9 KeyError. Russian i18n; VDS optimized.

## Week of 2026-06-01
Fixed QUIK archive (gzip append); completed P&L divergence diag (commission 2x overstated: 9.9 vs 5.8 /lot); daypnl.go/_test.go written; fixes pending.

## Week of 2026-05-25
Deployed archive gzip fix (JSONL); recovered corrupt .gz files, fixed dup key settings.json; merged branches (prod unblocked); preserved devmail svc on feat/devmail-live-session; mail delivery pending restart.