# Recent

## 2026-08-09
Smart-order UI overhaul (4-type rename: sl/tp/trail_tp/on_fill; rendering/label fixes; SL/TP+trailing display). MiniChart legend/labels; price fonts restored. Bollinger_bo campaigns: GDU6 11% (+358k), MXU6 15% (+143k). Fixed chart freeze, exchange_lag=-1 signal bug. Diagnosed batch slowdown: ema2 warmup 402→1600 bars violates 600-bar persistence; 5 degen bots disabled.

## 2026-08-10
Deployed min_gap_atr & inter-window msg API; completed first opt campaign (272 combos RIU6) with k_avg as main driver. Queued verification (144 combos) and stop-loss sweep (112 combos). UI fixes: chart coordinates/zoom/height/scroll, companion panel (DPI/monitor), ORDERS frame with type-grouping & gesture controls; fixed robot-card labels, order-xfer settings loss, lamp filter warmup bug.

## 2026-08-11
Fixed chart coordinates & deployed UI refresh (price scale, time axis, +25% height, curve-switcher, aligned candles); campaign-honest complete—bollinger_bo stable BRU6-BRQ6, order_block unreproducible. Diagnosed STL proxy allowlist bug (backtest gate corrections, 4 incident criteria). Completed candidate_gate.py validation; designed 4-phase mktdata collector (tape→L1→orders→L10, ~1GB/yr); fixed devmsg service (15-min alerts, Telegram escalation), enabled recorder.py.