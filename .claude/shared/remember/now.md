
## 18:15 | main
Fixed devmail_hook.py to handle missing STL_WINDOW (was silent, now prints board), extended sync to all 3 windows (real-trade/backtests/ui-ux), live-verified—blocked on manual restart of backtests/ui-ux windows to load new config.
## 18:17 | main
Deployed devchat.html to prod (stl.shectory.ru/devchat.html, commit baa0d18, 280 tests, live-verified); began smart-order-help.ts type-ref fixes (trail_tp/on_fill) on 14h task.