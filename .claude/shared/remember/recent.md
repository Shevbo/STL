# Recent

## 2026-08-12
Archive incident (data loss 10:22-19:52 from gzip-append) fixed: JSONL rewrite, 2538 stack/1993 tick recovered, async cycle deployed, prod merged. Dev infra: 4th STL window (SSH continuity), memsync.sh (cross-machine sync, path-collision fix). SMS hardened (3×30s retry, UTF-8, TG federation); trailing-stop backend prod (trail_sl, auto old-stop removal); dev chat endpoints ready, UI impl pending.

## 2026-08-11
Fixed chart coordinates & deployed UI refresh (price scale, time axis, +25% height, curve-switcher, aligned candles); campaign-honest complete—bollinger_bo stable BRU6-BRQ6, order_block unreproducible. Diagnosed STL proxy allowlist bug (backtest gate corrections, 4 incident criteria). Completed candidate_gate.py validation; designed 4-phase mktdata collector (tape→L1→orders→L10, ~1GB/yr); fixed devmsg service (15-min alerts, Telegram escalation), enabled recorder.py.

## 2026-08-10
Deployed min_gap_atr & inter-window msg API; completed first opt campaign (272 combos RIU6) with k_avg as main driver. Queued verification (144 combos) and stop-loss sweep (112 combos). UI fixes: chart coordinates/zoom/height/scroll, companion panel (DPI/monitor), ORDERS frame with type-grouping & gesture controls; fixed robot-card labels, order-xfer settings loss, lamp filter warmup bug.