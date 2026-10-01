# Archive

## Week of 2026-09-22
Deployed runner rev 1790020334 (P&L desync fix, 1151 tests, live) + state-tracking infra (truth.py/pos.py). QUIK L2 overload crash 25.09 fixed (260k trades recovered); deployed kill-switch, smart-orders quarantine, stubs regen, L2 resub. Resolved entry_reasons loss & Lua subscription issues (rev 1790410029, 1790457130).

## Week of 2026-09-15
S2-S5 series launched (S3-S4 done, S5 QUIK error); agent-si2ema-SiZ6-v1 paper-robot deployed (11 live). Critical price/points bug fixed & UI deployed; trailing-take/smart-orders ready; native-protect at 1048 green. Runner/agent/9 robots released; paper-lxk22 tested. Bar compression, warmup opt, RIU6/RIZ6 done; warehouse loss under investigation; blocked route/GZ.

## Week of 2026-09-08
Email cron (7d retention, session-only) + shectory-trader restart; backtest tracking (cross_only, sl_pct_l/sl_pct_s) + leaderboard verification (RIU6/RIM6).

## Week of 2026-09-04
Identified commission root causes: 2× overstated (9.9 vs 5.8 /lot), account ВМ excludes manual orders; daypnl.go written, commission.py fixes pending; 24h+ P&L divergence diagnosis ongoing. Investigated trading bot stop-loss loss: MACD whipsaw (not bug); amplitude filter backtest (1750pts RIU6) submitted. Updated quik_smart_orders.py with test coverage.