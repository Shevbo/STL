---
name: project_ai46_live
description: team-46 AI strategy is LIVE in paper mode on the hoster (Phase 7b done)
metadata: 
  node_type: memory
  type: project
  originSessionId: 996e0365-3372-49eb-945c-84e68f63d019
---

team-46 ("MOEX AI Trading Bot — team-46") runs LIVE in PAPER mode on the hoster since 2026-06-20.

**STANDALONE PROCESS (2026-07-05, commits bf9c654/3995c9b):** AI46 no longer runs inside
the STL uvicorn. py-spy proved hmm_regime (pure-Python Baum-Welch x20 symbols per 60s tick)
held the API event loop at 100% bursts -> every endpoint (robots-mirror etc.) timed out up
to ~30s. Two fixes: (1) model_refresh_secs=300 live default (env AI46_MODEL_REFRESH_SECS);
(2) extracted to `python -m trader.lab.ai46` + systemd unit shectory-ai46.service (Nice=10,
CPUWeight=50, Restart=always) with AI46_ENABLED=0 in the API env. After: API CPU 5-8% flat,
mirror ~2ms. RULE: no strategy/model computation ever runs inside the API process — same
isolation principle as robots-on-agent ([[project-robot-on-quik-agent]]).

Phase 7b done and deployed: `Ai46Service` wired into `trader/api/app.py` lifespan, env-gated.

**How it runs:**
- Host env `/home/ubuntu/.shectory_trade.env`: `AI46_ENABLED=1`, `AI46_SYMBOL_COUNT=20`.
- Symbols: if `AI46_SYMBOLS` (comma list) unset, auto-selects top-N FORTS front contracts by today's turnover via `iss_loader.top_instruments(n)`.
- Other gates: `AI46_LLM_ENABLED` (default 1, via Lineman/Klod), `AI46_ORDER_FLOW` (default 1).
- Ticks every 60s; loads 1m bars via `trader.lab.runtime._load_bars_shared` (bare ticker); paper fills go to `live_trades` (robot_id `team-46`, qty=1, status='paper'), so it shows in the Showcase.

**Two bugs fixed during launch:**
- `order_flow._trade_to_tuple` called `F.unwrap_decimal` (features has no such name) → live trades stream crashed for every symbol. Fixed: import from `trader.util`.
- TradesStream now subscribes Finam gRPC with `@RTSX` form but keys `OrderFlow` by bare ticker so engine OFI lookups match the bars.

**Known limitation:** execution price = `bars[-1].close` from ISS 1m bars. On weekends ISS minute candles for these contracts may lag (no same-day bars yet), so price freezes at Friday close → paper PnL ~0 until fresh intraday bars flow. Live price source (quote/trades stream) is a future change, not done. See [[reference_forts_weekend_trading]].

LLM strictly via Lineman/Klod [[feedback_llm_via_lineman]]. Deploy via `bash deploy/deploy.sh` [[project_lab_mvp_state]].
