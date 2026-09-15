# Recent

## 2026-09-14 | main
Ported TSLab 2EMA backtest with ladder configs (1/1/32/32/32/32), validated OOS on 498 tests (Fable ✓, 120/123 EMA match, 1090 positions); added bidirectional stops. Expanded RI.txt to 2022–2026 (589K); config 2→64 closed (−1.35M pts 2025). Designed regime-switch protocol (Steps 0-3); Step 0 testing complete (all 15 strategies negative, 304 backtests); initiated regime_tf_gate.py.

## 2026-09-13 | main
Tested ~10 strategy variants (rich_fool, impulse_fade, 2EMA, MACD, valley_spike) with high hypothesis-rejection velocity; OOS failures driven by gap-at-open losses and lookahead artifacts. DeskBot layers (RSI, averaging, trailing) implemented; 492 tests pass, e2l 156 variants queued. R-inversion impl'd for trade-direction flip; R-chaos sweep ongoing (9/36 leads).

## Identity Candidates
- IDENTITY CANDIDATE: Deployed shectory-trader with 6 fixes, improved Lua GC to 9.1h stable, and reduced downtime to <1m.