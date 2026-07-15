# Ponytail Audit — Shectory Trade & Lab

**Date:** 2026-07-15
**Scope:** whole repo (trader/ Python, quik_agent/ Go, robot_runner/ Python, frontend/ Svelte, scripts/deploy/proto/root).
**Focus:** over-engineering only — dead/zombie code, reinvented stdlib, unneeded deps, single-impl abstractions, dead GUI. Correctness, security, and performance are out of scope.
**Method:** six parallel deep-hunt agents, one per area. Every "dead" claim was grep-verified for callers across all four codebases (Python strategies run 1:1 in the runner, protos are shared, frontend calls endpoints by URL string). CLAUDE.md intentional-design was spared.
**Status:** APPLIED 2026-07-15 (33/34; #26 explain KEPT — showcase renders the rich FVG dict; #34 /qa kept pending operator decision; #21 fixed by WIRING FlattenRobot into recvLoop — it was a live bug, not dead code). Verification: pytest 381, go test 14 pkgs, vitest 24, builds green.

Ranked biggest cut first. Tags: `delete` (dead code) / `stdlib` (hand-rolled stdlib) / `native` (platform already does it) / `yagni` (single-impl abstraction) / `shrink` (fewer lines, same logic).

---

## Git weight (dead binaries/artifacts in history)

- `delete` **stl-backup.tar.gz** — 28.8 MB backup tarball tracked in git, in every clone. `git rm` + .gitignore. [stl-backup.tar.gz] (~29 MB)
- `delete` **opt-result.jpeg** — one-off optimizer screenshot. `git rm`. [opt-result.jpeg] (~163 KB)
- `delete` **test-results/.last-run.json + deploy.log** — generated Playwright/build artifacts. `git rm` + gitignore. [test-results/, deploy.log]

## Tech conflict / resource battle ("конфликты технологий")

- `delete` **prisma/** — dead ORM. Real DB layer is SQLAlchemy/asyncpg (trader/db.py); zero PrismaClient imports anywhere. Drop dir + `db:generate`/`db:migrate` in package.json. [prisma/, package.json] (~58 KB, -1 npm subsystem)
- `delete` **trader/broker/ built-but-unconsumed** — `app.state.broker` is set+logged, read by nothing; robots run via runtime.py/robot_runner, not BrokerInterface. Concrete safe cut: demo_robot.py (test-only). Interface/adapters are CLAUDE-protected (multiple real adapters declared). [trader/broker/demo_robot.py] (~150; dormant layer ~1400)

## Dead code — Python (trader/lab + ai46)

- `delete` **~500 lines of unwired indicators** in ai46/features.py — FeatureEngine calls only ema/last/rsi/vwap/volume_ratio/ofi; macd, bollinger, atr, adx, alpha6/12/41/101, ts_correlation, cross_asset_correlation, stochastic, realized_vol, volume_profile, the support_resistance cluster (_pivot_samples/_score_clusters/_median/SRSource/Level/PivotSample), fair_value_gaps, order_blocks, classic_pivots + dataclasses are test-only. [trader/lab/ai46/features.py:63-632] (~500)
- `delete` **entire news pipeline** — news.py (RSSCollector/parse_rss/detect_ticker/classify_severity/NewsItem) imported only by tests; Detector.classify_news_signal (only DET.NEWS consumer) test-only too. [trader/lab/ai46/news.py, detector.py:153] (~137)
- `delete` **5 unused LLM entry points** + dataclasses — only evaluate_proposal is wired; critic_verify/analyze_event/evaluate_exit/maybe_gate/classify_news (+ CriticVerdict/EventDecision/ExitVerdict/GateResult/NewsClassification) dead. [trader/lab/ai46/llm.py:119-265] (~135)
- `delete` **single-combo backtest subprocess path** — run_backtest_isolated + _subprocess_run have no caller; app.py uses run_backtest_grid only. [trader/lab/backtest.py:216-260] (~48)
- `delete` **4 unused pydantic models** (StlLink/LiveMetric/LiveTrade/BacktestResult) + test-only BacktestRun; only Robot is used. [trader/lab/models.py:8,36,48,61,73] (~48)
- `delete` **OrderFlow.on_book half** — never fed, so mlofi/queue_imbalance/microprice/spread_bps/snapshot/book/stats dead-in-practice (only ofi/on_trade fed). [trader/lab/ai46/order_flow.py:59-98] (~40)
- `delete` **conformal_interval + ConformalResult** — reconstructed, never called. [trader/lab/ai46/models.py:286-312] (~27)
- `delete` **small dead members** — CUSUMDetector.reset/recalibrate, ContrarianSession.cleanup, RiskManager.ref_price/record_pnl, BotParams.as_dict, indicators.donchian, md/source.py MarketDataSource Protocol (no impl). [ai46/*, trader/md/source.py] (~35)

## Dead GUI ("мёртвые функции GUI")

- `delete` **Optimizer.svelte — entire component dead** — never imported, no `?optimizer=` route; the Botstore "optimizer" hits are an unrelated background-sweep panel. [frontend/src/components/lab/Optimizer.svelte] (~398)
- `delete` **lab-analytics.ts dead cluster** — replay/computeStats/positionEpisodes/buildConnectors + LedgerRow/RoundTrip/Stats/Episode, superseded by tradeEvents/positionRects/rolledPnl. [frontend/src/lib/lab-analytics.ts] (~170)
- `yagni` **10x store `reset()` methods, zero callers** — no logout/clear flow exists. [frontend/src/lib/stores/*.svelte.ts] (~16)
- `delete` **unused frontend exports** — atrFromBars (strategy-help.ts:280), TradeMarker type (types.ts:38), fetchPortfolio + Position import (api.ts:26), robotsStore.updatePnl (robots.svelte.ts:9), leftover console.log (Botstore.svelte:87). (~32)
- `native` **fetch-auth `new Headers(...)` no-op** — collapse to `fetch(url,{...options,credentials:'include'})`. [frontend/src/lib/fetch-auth.ts:5] (~1)

## Zombie branches / dead scaffolding

- `delete` **optimize_campaign.py** — successor optimize_adaptive.py docstring says "SUPERSEDES the old in-process optimize_campaign.py"; no importers. [scripts/optimize_campaign.py] (~372)
- `delete` **archive/lab-devtools/** — explicitly archived; nothing in frontend/src imports offline-player/CodeEditor/LabBar. Git history is the archive. [archive/lab-devtools/*] (~410, 5 files)
- `yagni` **enqueue_campaign.py** — 0 live refs; CLAUDE.md documents queue_campaign.py (untracked successor) as THE campaign-queue tool. Confirm old copy, then drop. [scripts/enqueue_campaign.py] (~130)
- `delete` **_drain_commands + _pump no-op** — pump just `sleep(3600)` in a loop ("kept for symmetry/future use"); Session loop already flushes commands inline. [trader/quik/server.py:186-318] (~17)
- `native` **FlattenRobot branch unreachable** — recvLoop's robot dispatch lists only Deploy/Undeploy/SetParams/Pause/Start, never Flatten. Wire it into recvLoop or drop the branch; don't leave it dangling. [quik_agent/internal/link/robots.go:51] (~4)

## Reinvented stdlib ("велосипеды")

- `stdlib` **equalPositions/equalOrders** hand-rolled slice compare → `slices.Equal` (both element types comparable). [quik_agent/internal/accounts/accounts.go:186,198] (~22)
- `native` **max2(a,b int)** → builtin `max` (module is go 1.22, has it). [quik_agent/internal/quikdde/provider.go:41] (~6)
- `shrink` **explain.py _fvg_explain + 1-entry _EXPLAINERS** — mirrors sig_fvg by hand; _generic_explain already runs the real REGISTRY['fvg'] signal read-only. Collapse. CAVEAT: keep if the showcase panel renders the rich features dict / verbose RU "waiting_for" text. [robot_runner/explain.py:14-60] (~45)

## Dead Go members (all grep-verified: no caller incl tests)

- `delete` Bridge.Connected + bridgeAPI.Connected; Guard.WorstPrice (test-only); Manager.ClearBlock; Manager.Blocked; Guard.PlacedToday (test-only); Limits.String; Monitor.Prev; luaParam.margin field + plumbing (written, never read); Config.DiagIntervalSec (set, never read); watchdog Deps.OnReconnectAttempt (never wired). [quik_agent/internal/{trade,health,config,watchdog}/…] (~55)

## Small dup / shrink (Python)

- `yagni` **_resolve_agent duplicated** in quik_orders.py + quik_robots.py (+ same heuristic a 3rd time in store._pick). One shared helper in trader/quik/. [trader/api/quik_orders.py:49-74, quik_robots.py:43-53] (~20)
- `yagni` **sms_stub** — Phase-2 SMS placeholder that only logs "would SMS". Delete fn + CRITICAL call site. [trader/quik/alerts.py:61-77] (~20)
- `shrink` **_score/_cand** local byte-copies of module _campaign_score/_campaign_candidate (drift risk); **TokenResponse.access_token** alias of .token; **session-cookie const** defined 3x + 30-day TTL twice. [app.py:2991, auth/models.py:11, auth/guard.py:7, portal.py:13] (~11)
- `delete` **runner** recent_fills() (retarget tests to fills_tail), BridgeClient.aclose (test-only), collar param (never passed). [robot_runner/*] (~6)

## Stale docs (low priority)

- `delete` SPRINT_02_DEPLOYMENT_STATUS.md, SPRINT_02_VERIFICATION_REPORT.md, docs/Boris_Sprints/* — unreferenced. (~11 KB)
- `yagni` test_ws_hub_sprint1.py — sprint-named parallel WsHub test; merge cases into test_ws_hub.py, drop the file. [tests/api/]

## Conditional (largest, lowest confidence)

- `delete?` **/qa operator checklist page** (288-line inline HTML form) — no programmatic caller; reachable only by an operator typing `/qa`. Already self-contained/minimal. Cut ONLY if the operator no longer uses it as an acceptance tool. [trader/api/qa_routes.py:1-288] (~288)

---

**net: ~-2,900 lines confident (~-3,200 with /qa), -29 MB git blobs, -1 dep subsystem (Prisma), ~50 dead symbols/files.**

## Spared by design (checked, NOT flagged)

DDE reader / XlTable / `quikdde` sheet machinery (default-off, retained fallback); `quikdde` package name (historical — it is the market-data hub Provider); app.py `market_bars` hot-path caches (ISS tail TTL + agent_bars mtime parse cache — load-bearing); ai46 process isolation (standalone service by design); both strategy families (parametric registry + standalone modules, all in list_strategies, run 1:1 by robot_runner); STLRuntime protocol stubs in the runner (get_quote/get_orderbook/get_account); proto-contract Go handlers (ReplaceOrder/StartExecution/execution.go maker loop — dispatched from recvLoop, covered by tests); one-impl Go interfaces with real test fakes / import-cycle breaks; env-only pydantic-settings config; dual trading-enabled flag (safety, not redundancy); scripts/opt_agent.py (i9 pull-agent), regen_proto.sh + fix_grpc_imports.py (Finam stub chain), optimize_adaptive.py chain, ai46 backtest scripts; chart-time.ts MSK hand-formatting (lightweight-charts needs a sync formatter, data stays UTC).

---

## Findings

Priority key: **High** = high value, ~zero runtime risk, do first. **Medium** = real cut, needs a test/tests trimmed or a small decision. **Low** = minor tidy or needs an operator/design call.

| # | Issue | Priority | Plan to fix |
|---|-------|----------|-------------|
| 1 | stl-backup.tar.gz — 28.8 MB backup tarball tracked in git | High | `git rm` the file; add `*.tar.gz` to .gitignore. History rewrite optional (BFG/filter-repo) if clone size matters. |
| 2 | opt-result.jpeg — one-off screenshot in git | High | `git rm opt-result.jpeg`; gitignore `*.jpeg`. |
| 3 | test-results/.last-run.json + deploy.log — generated artifacts in git | High | `git rm -r test-results deploy.log`; add both to .gitignore. |
| 4 | prisma/ — dead ORM (real DB is SQLAlchemy/asyncpg) | High | Delete prisma/; remove `db:generate`/`db:migrate` from package.json scripts. Already confirmed zero PrismaClient imports. |
| 5 | Optimizer.svelte — entire dead GUI component (never imported, no route) | High | Delete the file. Grep once more for `Optimizer` import before removing. |
| 6 | scripts/optimize_campaign.py — superseded by optimize_adaptive.py | High | Delete the file (successor's docstring declares the supersession; no importers). |
| 7 | archive/lab-devtools/ — explicitly archived, nothing imports it | High | `git rm -r archive/lab-devtools`. Git history preserves it. |
| 8 | ai46/features.py — ~500 lines of unwired indicators (test-only refs) | Medium | Delete the unused indicator fns + dataclasses; remove or trim the unit tests that were their only callers. |
| 9 | ai46/news.py + Detector.classify_news_signal — news pipeline wired to nothing | Medium | Delete news.py; remove classify_news_signal + the DET.NEWS branch; drop the news tests. |
| 10 | ai46/llm.py — 5 unused entry points + result dataclasses | Medium | Delete critic_verify/analyze_event/evaluate_exit/maybe_gate/classify_news + their dataclasses; keep evaluate_proposal. |
| 11 | lab-analytics.ts — dead export cluster (replay/computeStats/positionEpisodes/buildConnectors + types) | Medium | Delete the four fns + LedgerRow/RoundTrip/Stats/Episode types; verify live exports (tradeEvents/positionRects/rolledPnl) untouched. |
| 12 | trader/broker/demo_robot.py — broker layer built at boot, consumed by nothing | Medium | Delete demo_robot.py. Separately decide whether the `app.state.broker` boot wiring stays (interface/adapters are CLAUDE-protected). |
| 13 | scripts/enqueue_campaign.py — superseded by queue_campaign.py | Medium | Diff against queue_campaign.py to confirm it is the old copy, then delete. |
| 14 | backtest.py — single-combo subprocess path (run_backtest_isolated + _subprocess_run) | Medium | Delete both; app.py uses run_backtest_grid only. |
| 15 | lab/models.py — 4 unused pydantic models (StlLink/LiveMetric/LiveTrade/BacktestResult) | Medium | Delete the four; move BacktestRun into the test that uses it or delete. |
| 16 | ai46/order_flow.py — on_book half never fed | Medium | Delete on_book + the book-only accessors (mlofi/queue_imbalance/microprice/spread_bps/snapshot/book/stats). |
| 17 | Dead Go members bundle (Connected/WorstPrice/ClearBlock/Blocked/PlacedToday/String/Prev/margin/DiagIntervalSec/OnReconnectAttempt) | Medium | Delete each symbol + the lone tests that exist only to cover WorstPrice/PlacedToday; run `go test ./...` on the hoster. |
| 18 | Store `reset()` x10 — zero callers | Medium | Remove the reset() method from each store in frontend/src/lib/stores/. |
| 19 | Unused frontend exports (atrFromBars, TradeMarker, fetchPortfolio, updatePnl, console.log) | Medium | Delete each export + the now-orphan Position import in api.ts; remove the debug console.log in Botstore.svelte:87. |
| 20 | server.py — _drain_commands + _pump no-op keeper task | Medium | Delete _drain_commands, _pump, and the cmd_task lifecycle; Session loop already flushes commands inline. |
| 21 | robots.go — FlattenRobot branch unreachable (recvLoop never dispatches it) | Medium | Decision required: wire Flatten into recvLoop's robot dispatch, or delete the dead case. Do not leave dangling. |
| 22 | ai46 small dead members (CUSUM.reset/recalibrate, ContrarianSession.cleanup, RiskManager.ref_price/record_pnl, BotParams.as_dict, indicators.donchian, md/source.py Protocol) | Low | Delete each; md/source.py MarketDataSource Protocol has no implementer, drop the file. |
| 23 | ai46/models.py — conformal_interval + ConformalResult never called | Low | Delete both. |
| 24 | accounts.go — equalPositions/equalOrders hand-rolled slice compare | Low | Replace both with `slices.Equal`; add `"slices"` import. |
| 25 | provider.go — max2 helper | Low | Replace with builtin `max` (go 1.22); delete max2. |
| 26 | explain.py — _fvg_explain + 1-entry _EXPLAINERS duplicate sig_fvg | Low | Collapse explain() to _generic_explain (runs the real REGISTRY signal). KEEP if the showcase renders the rich features dict / verbose RU text — verify first. |
| 27 | _resolve_agent duplicated across quik_orders.py + quik_robots.py (+ store._pick) | Low | Extract one shared helper into trader/quik/; import in both routers. |
| 28 | alerts.py — sms_stub Phase-2 placeholder that only logs | Low | Delete the fn + the severity==CRITICAL call site. |
| 29 | app.py _score/_cand local copies; TokenResponse.access_token alias; session-cookie const x3 | Low | Call module _campaign_score/_campaign_candidate; use .token directly; define the cookie name + TTL once and import. |
| 30 | robot_runner — recent_fills()/aclose()/collar param unused in prod | Low | Delete recent_fills (retarget ~3 tests to fills_tail()[-20:]); delete aclose; inline collar=0.002 into PlaceOrder and drop the param. |
| 31 | fetch-auth.ts — new Headers(...) no-op reconstruction | Low | Collapse to `return fetch(url, { ...options, credentials: 'include' });`. |
| 32 | Stale docs: SPRINT_02_*.md, docs/Boris_Sprints/* | Low | `git rm` both root sprint docs + docs/Boris_Sprints/; confirm no CONTRACT.md reference first (sprint02 docs in docs/ ARE referenced — keep those). |
| 33 | test_ws_hub_sprint1.py — sprint-named parallel test file | Low | Merge its cases into test_ws_hub.py; delete the sprint file (tests pass, so merge not delete). |
| 34 | /qa operator checklist page (288-line inline HTML) — no programmatic caller | Low | Conditional: confirm with the operator that /qa is no longer used as an acceptance tool, then delete qa_routes.py. Keep otherwise. |
