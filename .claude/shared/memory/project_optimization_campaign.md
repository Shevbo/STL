---
name: project-optimization-campaign
description: "5h unattended LAB parameter-optimization campaign — state, how to monitor, results"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54d0cf23-21ef-4926-81c5-db49d8bc51ce
---

# LAB Optimization Campaign (5h unattended)

User asked (2026-05-30 ~21:35 UTC) to run robot/param optimization non-stop for 5 hours,
sampling ~every 3rd variant randomly, random-search for large grids, building a hit-parade
of candidates for real (live) launch.

## What runs
- Script: `scripts/optimize_campaign.py [hours]` (committed 3df8eb5). Runs IN-PROCESS backtests
  (no subprocess) over all 4 strategies × 3 symbols (RIM6, SiM6, GZM6), 2026-03-01..05-25.
- Generation loop = Monte-Carlo random search: each generation draws fresh random ~1/3 subset
  (KEEP_FRACTION=1/3) of each strategy's valid grid, capped MAX_PER_TASK=4000; dedupes via `seen`;
  repeats until 5h budget or full coverage.
- Writes every result to DB table `optimization_leaderboard` (campaign_run, strategy, symbol,
  params JSONB, total_return, sharpe, max_drawdown, win_rate, total_trades, score, candidate).
- score = sharpe + 3*return - 2*drawdown. candidate = return>0 AND sharpe>=0.5 AND dd<=0.15
  AND 30<=trades<=3000.

## Launch state (this run)
- PID 1283344 on Hoster (83.69.248.175), started 21:35:10 UTC 2026-05-30, budget 5h → ends ~02:35 UTC.
- Log: `/home/ubuntu/campaign.log`
- Started via: nohup … python scripts/optimize_campaign.py 5 (LAB_DB_URL env inline).
- Throughput ~30 backtests/sec (smoke: 1631 evals/~50s, 12 candidates).

## How to monitor (sequential ssh — NEVER parallel ssh, causes cascade cancel)
- Progress:  ssh hoster "tail -5 /home/ubuntu/campaign.log"
- Counts:    ssh hoster "sudo -u postgres psql project_stl -t -c 'SELECT count(*), count(*) FILTER (WHERE candidate) FROM optimization_leaderboard;'"
- Alive?:    ssh hoster "ps -p 1283344 -o pid,etime,cmd --no-headers"
- Hit-parade:
  ssh hoster "sudo -u postgres psql project_stl -c \"SELECT strategy,symbol,params,round((total_return*100)::numeric,2) ret,round(sharpe::numeric,2) sh,round((max_drawdown*100)::numeric,1) dd,total_trades n,round(score::numeric,3) score FROM optimization_leaderboard WHERE candidate ORDER BY score DESC LIMIT 25;\""

## Smoke-test top candidates (pre-full-run, for sanity)
- rsi_mean_reversion SiM6 {period18,oversold24,overbought74} ret 6.41% sh 1.34 dd 3.2% n142
- donchian_breakout  SiM6 {entry118,exit34} ret 5.12% sh 1.08 dd 4.1% n88
- ema_crossover      GZM6 {fast7,slow63} ret 4.30% sh 0.91 dd 3.8% n56
- supertrend         SiM6 {atr34,mult52} ret 3.95% sh 0.87 dd 4.6% n41

## RESULT — campaign COMPLETE (ran full 5h, 21:21→02:22 UTC 2026-05-31)
evaluated=18300, candidates=839. Process exited cleanly (final TOP block in campaign.log).
Robust hit-parade (filter: candidate AND trades>=50 AND return>=2%, ORDER BY score):
1. supertrend RIM6 atr=6 mult=51 → ret 9.00% sh 3.06 dd 1.9% n63
2. rsi_mean_reversion RIM6 period13 OS10 OB60 → ret 3.46% sh 3.12 dd 1.4% n95
3. rsi_mean_reversion RIM6 period15 OS12 OB60 → ret 4.35% sh 2.70 dd 1.5% n84
4. rsi_mean_reversion RIM6 period30 OS26 OB76 → ret 8.53% sh 2.62 dd 4.3% n49
5. ema_crossover RIM6 fast38 slow131 → ret 11.71% sh 2.42 dd 3.7% n112  (highest return)
6. rsi_mean_reversion GZM6 period16 OS14 OB62 → ret 0.66% sh 2.67 dd 0.2% n106 (tiny return, skip)
7. supertrend RIM6 atr8 mult48 → ret 7.20% sh 2.30 dd 2.2% n78
Top-3 picks for forward-test: supertrend RIM6 6/51; ema RIM6 38/131 (best return); rsi RIM6 13/10/60 (best risk).
Full table in DB optimization_leaderboard (campaign_run='20260530-212130'). Frontend has no UI to view it yet — TODO add a leaderboard tab.

## RUN 2 — 16-STRATEGY LIBRARY (built 2026-05-31, commit 99bacd9)
User pushed back: "4 strategies isn't dozens of robots; you have a 1000+ source."
Built trader/lab/strategies/library.py: REGISTRY of classic indicator robots + make_on_bar(rid)
(each robot = signal fn returning desired signed pos; shared executor places orders).
12 new robots: macd_cross, bollinger_mr, bollinger_bo, stochastic, cci, williams_r, momentum,
roc, triple_sma, keltner_bo, rsi_trend, ema_atr. indicators.py expanded (sma/bollinger/macd/
stochastic/cci/momentum/roc/williams_r/donchian/keltner/stdev). Campaign auto-derives param
space from each schema (_add_library_specs) → 16 strategies × 3 symbols = 48 tasks,
119,748 combos/generation. SMOKE-TESTED OK on server: all 16 load + write rows; first candidate
ema_crossover RIM6 fast36/slow39 +12.98% Sharpe 3.13.

### RUN 2 LIVE: campaign_run 20260531-063704 (server clock), started ~06:37 server time, 5h budget.
16 strategies × 3 symbols = 48 tasks, 119748 combos/generation. Writing rows (120 in first min).
log /home/ubuntu/campaign.log. CRITICAL ssh gotcha SOLVED: pkill -f optimize_campaign self-matches
the ssh shell → kills session → exit 255. ALWAYS use pkill -9 -f 'optimize[_]campaign' (bracket
self-exclusion). Run ssh commands ONE AT A TIME (parallel batch cascade-cancels on first nonzero).

### (prior) OPEN ITEM — confirm/relaunch full 5h run for 16 strategies.
Server HEAD synced to 99bacd9. To (re)launch cleanly:
  ssh hoster "pkill -9 -f optimize_campaign; sleep 2; sudo -u postgres psql project_stl -c 'TRUNCATE optimization_leaderboard;'"
  ssh hoster "cd /home/ubuntu/apps/shectory-trader && export LAB_DB_URL='postgresql://project_stl_app:f7306cb2ab5c500ffc6fb0349377621d@localhost:5432/project_stl' && nohup <venv>/bin/python3 scripts/optimize_campaign.py 5 > /home/ubuntu/campaign.log 2>&1 & echo PID \$!"
venv = /home/ubuntu/.cache/pypoetry/virtualenvs/shectory-trader-Ik0M11VW-py3.12
At 2026-05-31 ~22:20 UTC hit a HARNESS output-capture glitch (even local echo returned empty) —
not a server problem. Retry when capture works.
NOTE: the 1000+ in sources are Pine/Lua/C# — can't auto-import; they ARE variations of these
classic patterns. Library = the importable realization. Future: Ichimoku, ParabolicSAR, ADX, VWAP,
Heikin-Ashi + frontend Leaderboard tab.

## RUN 3 — MONEY-CORRECT + BOTSTORE UI (2026-05-31, PID 1398201, ends ~11:53 UTC)
MAJOR FIX: backtest PnL was in INDEX POINTS, not rubles. Now correct:
- iss_loader.fetch_contract_spec() pulls MOEX ISS spec (free): MINSTEP, STEPPRICE, INITIALMARGIN.
- point_value = STEPPRICE/MINSTEP (RIM6=1.42, SiM6=1.0, GZM6=1.0, BR=710, GD=71). PnL ×= point_value → RUB.
- BacktestRuntime(point_value=...), compute_metrics(point_value=...), run_single_backtest/grid threaded.
- market_store.refresh_instrument_spec() caches into instrument_meta (added point_value col).
- Real ГО per contract from ISS (RIM6=24838). Averaging → margin = maxAbsPos × GO (signed-pos engine already tracks qty).
- compute_metrics rewrote to handle long+short round-trips + recovery_factor + net_profit (RUB).
- leaderboard table got cols: net_profit, recovery_factor, point_value, initial_margin, initial_equity, date_from/to.
VERIFIED per-symbol pv/margin correct in DB (RIM6 1.42/24838, SiM6 1.0/10883, GZM6 1.0/2034 — no bleed).

BOTSTORE UI: new tab in LabPanel → frontend/src/components/lab/Botstore.svelte. Reads GET /api/v1/botstore
(app.py): catalog of all 16 robots, per-robot variants_tested, last_run, best result per symbol (return %,
net RUB, maxDD, recovery, period, params), expandable per-symbol rows. Preamble: "доходность от первонач.
инвестиций 100 000 ₽". Same preamble added to BacktestLab + Optimizer ("везде"). /api/v1/strategies now also
appends the 12-robot library (list_strategies()). Verified in shared browser: 16 robots, 2847+ variants,
EMA RIM6 14.21%/14210₽, RSI 9.83%, SuperTrend 8.55%.
INITIAL_EQUITY constant = 100000 (campaign + Botstore preamble + UI). To change basis, update both.

## RUN 3 LIVE + BOTSTORE VERIFIED (2026-05-31 ~07:21 server / campaign_run 20260531-072058)
Two bugs fixed during this run: (a) campaign asyncpg pool lacked JSON codec → crash writing JSONB
`raw` dict (added _init_codec, params passed as dict not json.dumps); (b) LabPanel didn't route
'botstore' activeTab → fell through to BacktestLab (added {:else if activeTab==='botstore'}).
Botstore tab VERIFIED in shared browser: preamble shows "100 000 ₽" basis, 16 robots, 240 variants,
columns Робот/Вариантов/Послед.прогон/Лучший инстр./Период/Лучшие параметры/Доходность/Чистыми ₽/
Макс.просадка/Фактор восст., expandable per-symbol rows. Money correct (RIM6 pv 1.42, GO 24838).
Campaign 5h running, PID family 1338658, ends ~12:21 server time. Botstore auto-refreshes as it fills.

## RUN 3 COMPLETE (2026-05-31 12:22 server, full 5h) — FIRST MONEY-CORRECT LEADERBOARD
evaluated=17539, candidates=921, all 16 strategies covered ~1170 variants each (momentum only 57 = small space).
Strategies with 0 candidates: macd_cross, triple_sma, stochastic, williams_r, ema_atr, momentum (poor on this data).
TOP HIT-PARADE (RUB, filter trades>=50 & return>=2%, score desc), basis 100000 ₽:
1. supertrend RIM6 atr15/mult60 → +23.03% = +23031₽, dd4.7%, RF 4.93, n54, GO24838
2. bollinger_bo SiM6 mult36/period60 → +14.27% = +14266₽, dd2.1%, RF 6.81, n95, GO10883  (best risk-adj)
3. bollinger_bo RIM6 mult40/period60 → +13.51% = +13507₽, dd3.6%, RF 3.75, n52
4. supertrend SiM6 atr18/mult54 → +10.32% = +10318₽, dd2.5%, RF 4.18, n62
5. bollinger_bo RIM6 mult38/period39 → +20.41% = +20405₽, dd7.8%, RF 2.62, n48
cci RIM6 period33/thr158 → +4.42%/4424₽ RF 7.16 (highest recovery factor, low dd).
Winners cluster: SuperTrend + Bollinger Breakout (trend strategies) on RIM6/SiM6. Mean-revert (RSI, CCI)
safer but smaller. STILL in-sample one window — forward-test before live. Botstore tab shows all this live.

## Caveat to tell user
All in-sample on ONE 2.5-month window — no walk-forward (v2). High Sharpe here = optimization
on one period; not proof of live edge. Hit-parade is a SHORTLIST to forward-test, not deploy blindly.

See [[project-lab-mvp-state]].
