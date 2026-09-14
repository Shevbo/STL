# Recent

## 2026-09-13 | main
Tested ~10 strategy variants (rich_fool, impulse_fade, 2EMA, MACD, valley_spike) with high hypothesis-rejection velocity; OOS failures driven by gap-at-open losses and lookahead artifacts. DeskBot layers (RSI, averaging, trailing) implemented; 492 tests pass, e2l 156 variants queued. R-inversion impl'd for trade-direction flip; R-chaos sweep ongoing (9/36 leads).

## 2026-09-12 | main
Optimized rich_fool strategy (8 waves: rf4-rf10, 500k+ samples); fixed d_coef regime-tuning, volume, timing, stop-levels. Deployed d_coef sweep (0.05–1.0) with anti-skew protection & trailing-stop refinements; discovered point_value DB NULL affecting leaderboard reproducibility. Accelerated i9 1.7x→2x (217 runs/min); batch 1 complete, ETA 38h (was 74h). rf10 leaders analysis WIP; +76.3k commission verified; tests passing.

## 2026-09-11 | main
Deployed shectory-trader 1787379836 (6 fixes: Lua GC stable 9.1h vs 870 MB/h prior, order cap, fixation alert, mem metrics, hourly guard, journal healing); <1m downtime; 9.3h uptime; fin +147,689 (+124). Implemented rich_fool strategy (description, ladder placement, multi-step execution), swing_trend (slow trend qty=1), and impulse_fade; fixed backtest queue issues and opt_agent.py (truncation, collision); queued 37 rechecks. Set up night queue scripts. Critical: bar cache window cut 31.07.2026 invalidated 182 tasks/45d (266k rows, 132 campaigns), rich_fool walk-forward closed (0/30 configs); requires i9 update_token before manifest sync run.

## Identity Candidates
- IDENTITY CANDIDATE: Deployed shectory-trader with 6 fixes, improved Lua GC to 9.1h stable, and reduced downtime to <1m.