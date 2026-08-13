---
name: project-live-fvg-robot
description: LIVE real-money FVG robot on RIU6 (1 contract max) — deploy/stop/monitor
metadata: 
  node_type: memory
  type: project
  originSessionId: 5c8ca1cf-01d5-4d75-8cc0-d0d930ea6369
---

**SUPERSEDED 2026-07-08:** this Finam-path `live-fvg-RIU6` is UNDEPLOYED (deployed=false).
Live real-money FVG now runs as `agent-fvg-RIU6-v2` ON THE QUIK AGENT (QUIK execution, not
Finam) — see [[project-robot-on-quik-agent]] for current state, controls, and config. Below
is the historical Finam deployment.

**LIVE REAL-MONEY robot deployed 2026-07-03:** `live-fvg-RIU6` — Fair Value Gap (ICT)
strategy trading RIU6 for REAL money via the Finam Trade API (NOT QUIK). Operator
(bshevelev75@gmail.com) explicitly authorized it. Created by a direct DB insert into the
`robots` table (there is NO API to set `state_json.live_real` — going live is a deliberate
DB-level action by design; the create/update robot APIs only touch params/script/schedule).

Config: `params_json` qty=1, avg_max=1 (avg_max=1 = averaging OFF → holds EXACTLY 1
contract; verified in trader/lab/strategies/library.py), symbol RIU6, tp_atr=60,
min_frac=12, avg_atr_n=5, avg_step_atr=24. `state_json` {"live_real": true}. schedule
09:00-23:55. stl_link_id=stl-finam-forts-01. deployed=true.

How it trades: scheduler reads state_json.live_real → LiveRuntime(paper=False) →
runtime.place_order sends a REAL OrderRequest via self._tx (Finam TX). A SELL that would
open/increase a short is SKIPPED unless the account is shortable (short preflight +
[666] uncovered-risk handling) — so if Finam margin-short is off, it trades LONG-ONLY.

STOP / kill-switch: `POST /api/v1/robots/live-fvg-RIU6/undeploy` (or the UI). To fully
disarm, set state_json.live_real=false in the DB. Monitor: its fills land in `live_trades`
(status != 'paper' = real) and in the Showcase/robot window. LAB has a **LIVE tab** (real-
money robots only, `deployed && !paper`) showing the Finam-link ping; the robot window has a
**latency pane** (outbound/inbound/RTT) under the chart. Ping = RTT of GET /v1/assets/clock
every 5s over the order HTTP/2 transport; logged to `latency_samples`, served by
`GET /api/v1/live/latency` (trader/latency.py). RobotWindow now self-polls /live every 15s.

CAVEAT: auto-rollover is NOT built (backlog #9) — before RIU6 expiry (~Sep 2026) roll
manually or build #9. Robots are NOT yet on BrokerInterface (they trade Finam directly via
LiveRuntime, not QuikBroker). See [[project-quik-agent-phase1]].
