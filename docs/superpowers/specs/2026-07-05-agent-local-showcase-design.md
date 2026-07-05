# Agent Local Showcase (VDS status page) — Design

Date: 2026-07-05
Status: approved by operator (chat), pending spec review

## Purpose

A compact, always-available view of the QUIK-side agent FOR THE OPERATOR ON THE VDS,
independent of STL. One browser tab next to QUIK answers: is the local platform healthy,
what is trading and how, does every robot's position/orders/trades/transactions match
QUIK fact, and it survives any restart. The same view is mirrored on STL (slower refresh)
so the code and experience are not VDS-only.

## Non-goals

- Not a trading terminal: the only write action is the operator-confirmed "Align" plan.
- No deploy/undeploy/params editing from this page (STL stays the control plane).
- No auth / remote exposure: the local server binds 127.0.0.1 only.
- Telegram alerting: backlog (needs operator token), not in this iteration.

## Architecture

New Go package `quik_agent/internal/status`:

- HTTP server on `127.0.0.1:8071` (port key `status_port` in `agent_config.json`, 0 = off).
- `GET /` — single embedded HTML page (go:embed, vanilla JS, no build step). Polls
  `/api/status` every 1s. Visual language borrows from the STL AgentRobotScreen
  (dark, freshness badges, red REAL plaque) but has zero frontend build dependencies.
- `GET /api/status` — one JSON snapshot (schema below). This schema is THE contract
  shared with STL.
- `GET /logs/agent`, `GET /logs/runner`, `GET /logs/robot/{id}` — last 64KB of the
  corresponding log file, plain text.
- `GET /strategy/{name}` — strategy description served from `strategies_doc.json`,
  a file exported by `deploy/build_runner.sh` from the strategy registry docstrings
  (`trader/lab/strategies/library.py`) and packed into the release zip next to
  `robot-runner.exe`. Works offline; the page also shows the online link
  `https://stl.shectory.ru/?agent_robot=<id>`.
- `POST /api/align` — execute a frozen align plan (see Reconciliation). The ONLY
  mutating endpoint.

Data sources are all in-process already: health snapshot (`internal/health`), luafeed
freshness, `robots.json` store, last `RobotStatusReport` per robot, `trade.Manager`
working orders, `Guard` limits, plus the new QUIK account tables (below).

## Status JSON schema (shared contract)

```json
{
  "generated_at_ms": 0,
  "agent": {"version": "", "build_rev": 0, "uptime_sec": 0, "master_flag": false},
  "health": {
    "feed":  {"state": "UP|DEGRADED|DOWN", "per_instrument_tick_age_ms": {"RIU6": 0}},
    "quik_rtt_ms": 0,
    "exchange_lag_ms": 0,
    "clock_drift_ms": 0,
    "stl_link": {"connected": false, "reconnects": 0, "note": "trading unaffected"}
  },
  "robots": [{
    "id": "", "strategy": "", "symbol": "", "mode": "paper|real",
    "params": {}, "position": 0, "avg_price": 0,
    "pnl_points": 0, "pnl_rub": 0, "point_value": 0,
    "working_orders": [], "trades_today": 0, "trades_total": 0,
    "deployed_at_ms": 0, "params_updated_at_ms": 0,
    "links": {"strategy": "/strategy/...", "log": "/logs/robot/...", "stl": "https://..."}
  }],
  "recon": {
    "state": "OK|MISMATCH|STALE",
    "checked_at_ms": 0,
    "positions": [{"symbol": "", "robots_sum": 0, "quik": 0, "ok": true}],
    "orders":    [{"order_num": "", "owner": "robot-id|ORPHAN|MISSING", "ok": true}],
    "trades":    [{"trade_id": "", "matched": true}],
    "trans":     [{"trans_id": 0, "status": "", "ok": true}],
    "align_plan": {"id": "", "steps": [{"kind": "cancel_order|close_position|adopt_fill|fix_state", "detail": ""}]}
  }
}
```

STL mirror: the agent sends this snapshot over the existing link as a new
`AgentMessage.StatusSnapshot` frame (change-gated + min interval 5s, per flush
discipline). STL stores it in `QuikAgentStore` and serves it verbatim at
`GET /api/v1/quik/agent-local-status`. The SAME embedded HTML page is copied into the
STL frontend as a static asset; the only difference is the poll target and interval
(1s local, 10s on STL). One schema, one page, two hosts.

## Health block (item 1)

- **Feed freshness**: per-instrument tick age ms from the luafeed provider (exists).
- **QUIK RTT**: agent writes a `ping` command to `C:\quik-bridge\cmd.jsonl`; the Lua
  script replies `pong` with its own timestamp on the event queue. RTT = round trip in
  ms, sampled every 5s. New tiny handler in `shectory_trade.lua`.
- **Exchange lag**: exchange timestamp of the freshest OnAllTrade record minus local
  receive time. Shown together with **clock drift**: local clock vs QUIK
  `getInfoParam("SERVERTIME")` (the VDS clock has drifted before; drift > 1500ms turns
  the badge yellow and the lag number is annotated as unreliable).
- **STL link**: connected/reconnects/uptime, explicitly labeled "does not affect trading".

## Robots table (items 2, 3)

One row per robot from `robots.json` + last `RobotStatusReport`: id, strategy, symbol,
mode (paper grey / REAL red), expandable params, position, avg price, P&L in points AND
rubles (`coef = step_cost / price_step` from the params feed; if coef is missing show
points only, never a fabricated ruble number), working orders, trades today/total,
`deployed_at`, `params_updated_at` (two NEW persisted fields in `robots.json`; deploy
sets the first, SetRobotParams updates the second), links to strategy doc and log.

## Reconciliation (item 4)

New Lua publications on the file queue (all change-gated):

- `acc_position`: `futures_client_holding` rows (symbol, net position, avg price), 2s.
- `acc_orders`: orders table, active + today's terminal (order_num, status, price, qty,
  balance, comment), 2s.
- `acc_trades`: today's account trades (trade_id, order_num, price, qty, time), 2s.
- `trans_reply` already flows.

New Go package `quik_agent/internal/recon`, pure logic + tests:

- **Positions**: sum of robot positions per symbol vs QUIK holding. Human/manual trades
  make these legitimately differ; the check compares against
  `quik_position - manual_offset`, where `manual_offset` is a persisted operator-set
  number per symbol (default 0, editable on the page, stored in `agent_config.json`).
- **Orders**: every robot working order must exist active in QUIK (else MISSING);
  every QUIK active order must be owned by a robot or the human path (else ORPHAN).
- **Trades**: every runner fill matches a QUIK trade by order_num (+qty/price);
  unmatched on either side is flagged.
- **Transactions**: hung/rejected trans replies surfaced (reuses phantom-recon state).

Recon runs every 5s on fresh tables; if account tables are older than 30s the whole
block shows STALE, never a false OK.

### Alerting

On OK→MISMATCH transition (debounced 10s to skip in-flight races): red banner + sound
on the page, `EmitAlert` to STL with new codes `RECON_MISMATCH` / `RECON_RECOVERED`
(WARN for paper-only robots, CRITICAL when a real robot is involved). STL surfaces it
through the existing alert path.

### Align (operator button)

The recon engine also produces a deterministic **align plan**: ordered steps of kinds
`cancel_order` (orphan), `close_position` (offsetting limit order at current price,
quantized to the price step), `adopt_fill` / `fix_state` (runner state corrections, no
orders). The plan is frozen with an id (hash of steps + snapshot time). The page shows
the plan verbatim under the mismatch. `POST /api/align {plan_id}`:

1. Agent recomputes recon; if the plan id no longer matches reality — reject, show new plan.
2. Order-bearing steps go through the normal `trade.Manager` path: Guard limits AND the
   master flag apply (a disarmed agent can only do state-only fixes).
3. Steps execute sequentially; the result of each is appended to the plan log and shown.

Align is local-page-only in this iteration; the STL mirror shows recon read-only. A
remote align command needs an `OrchestratorMessage` extension — deferred.

## Restart immunity (item 5)

- The server lives inside the agent process: agent autostart, self-update, and robot
  resume from `robots.json`/`runner_state.json` already cover VDS reboots.
- After a QUIK restart the Lua script republishes full account tables on start, so
  recon self-heals without any operator action.
- The page JS retries silently and shows a grey "agent unreachable" state instead of
  stale numbers (numbers older than 5s are visually dimmed).

## Testing

- Go unit tests: recon comparators (positions/orders/trades incl. manual_offset,
  ORPHAN/MISSING), align plan generation + plan-id invalidation, status JSON builder,
  STALE gating.
- Lua handler changes verified on the hoster build + operator smoke on the VDS
  (as with the tape rollout).
- STL side: pytest for the mirror endpoint; frontend static page is shared verbatim.

## Deploy

Usual chain: Go build on the hoster (`~/quik_build`, publish script), agent
self-updates; `shectory_trade.lua` update on the VDS is operator-manual. STL side is a
normal backend+frontend deploy (safe procedure).
