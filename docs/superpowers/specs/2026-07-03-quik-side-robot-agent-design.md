# QUIK-side robot execution agent — design

Date: 2026-07-03. Approved by operator (Boris) in brainstorming session.

## Problem

LIVE robots (e.g. `live-fvg-RIU6`, real money) execute INSIDE the STL uvicorn process on the
shared hoster VDS and trade via the Finam API. When STL goes down (2026-07-03: a deploy
build thrashed the VDS, SSH+HTTP dead ~15 min, operator hard-reboot), the robot stops
trading — missed signals and unmanaged position risk. STL is a single point of failure for
live execution.

## Goal

A universal QUIK-side agent that:
1. works fast against QUIK (local market data + local order path);
2. accepts a LIVE robot's working logic from the STL orchestrator (deploy);
3. reports its working status back to STL;
4. trades fully isolated from STL — keeps trading when STL is down;
5. obeys control commands from STL when they arrive — set params, pause, start,
   kill switch — scoped strictly to the agent's own robots.

## Decisions made (operator choices)

| Question | Decision |
|---|---|
| Strategy engine on the agent | **Embedded Python runner** reusing `trader/lab/strategies/library.py` 1:1 (live logic == backtest logic; zero porting divergence). Not a Go rewrite. |
| Order execution path | **Local QUIK bridge** (`sendTransaction` file-queue) + QUIK DDE data. Trading loop has zero cloud dependencies. Account = the one behind the QUIK terminal. |
| v1 rollout | **Straight to real money, 1 contract**, hard limits (whitelist, qty caps, collar, daily cap). Paper mode kept as a runner debug flag, default real. |
| Agent kill switch scope | **Halt + cancel all working orders; position is LEFT open** (operator closes manually). Standard exchange-style halt; no market-order panic close. |
| Agent↔runner IPC | **Local gRPC on 127.0.0.1** (typed, same proto stack, good for tick streaming). Not a file queue. |

## Topology (Windows VDS next to QUIK)

- **`quik-agent` (Go, existing)** — the only process talking to QUIK (DDE reader + Lua
  file-queue bridge) and to STL (dial-out bidi gRPC). Gains a local "runner bridge".
- **`robot-runner` (Python, new)** — executes strategies via the existing `LiveRuntime`
  machinery with a new broker adapter. Persists robot config+state locally; restarts clean.
- STL becomes control-plane + monitor only for agent-hosted robots.

```
QUIK terminal ── DDE ──> quik-agent (Go) ── gRPC dial-out ──> STL (orchestrator/monitor)
                 Lua bridge ^   │ 127.0.0.1 gRPC (runner bridge)
                 (cmd.jsonl)│   v
                          robot-runner (Python: library.py + LiveRuntime + AgentQuikBroker)
```

## Components & interfaces

### Go agent: runner bridge (new `internal/runner` or extension of `internal/trade`)
Local gRPC server on 127.0.0.1 exposing to the runner:
- **MarketData stream**: QUIK DDE ticks/quotes for whitelisted symbols.
- **PlaceOrder / CancelOrder** → existing `trade.Manager` (hard limits re-checked) → Lua bridge.
- **OrderEvents stream**: fills / OrderUpdate / TransReply back to the runner.
- **Control stream**: relays STL commands (deploy/params/pause/start/kill) to the runner.
- **StatusReport**: accepts runner status and forwards to STL over the existing link.

### Python runner
- `AgentQuikBroker` implements the existing `BrokerInterface` by calling the local agent gRPC.
- Bar building uses the SAME builder code as STL/backtest (signal parity).
- Strategy loop: existing `library.py` strategies + `LiveRuntime`, unchanged.
- Local persistence: robot spec + runtime state written atomically; resume after restart.
- Watchdog contract: agent supervises the runner process; if the runner dies, the agent
  restarts it and blocks new orders until the runner reports healthy.

## Robot logic handover (STL → agent)

- STL "deploys" a robot to the agent: sends `RobotSpec` (robot id, strategy_id, params,
  symbol, schedule, qty/risk caps) over the existing STL↔agent gRPC link.
- Agent persists the spec locally and hands it to the runner.
- **Local state is the runtime source of truth.** Once deployed, the runner trades from the
  local config+state regardless of STL availability. On STL reconnect, STL may re-push
  config (applied + ack'd) and reads status.
- Ownership split: config (strategy/params/schedule) is authored by STL (push); runtime
  (position/fills/paused) is authored by the agent (report). No conflict.

## Control commands (STL → agent → runner)

`DeployRobot`, `UndeployRobot`, `SetRobotParams`, `PauseRobot`, `StartRobot`, `KillSwitch`.
- All optional and idempotent; absence of STL never stops trading.
- KillSwitch: block new orders + cancel all the agent's working orders; positions left as-is.
  Scope: this agent's robots only. The agent also has a local kill trigger (file/hotkey),
  not dependent on STL.

## Status reporting (agent → STL)

`RobotStatusReport` per robot: position, avg price, working orders, recent fills,
running/paused, realized/unrealized P&L, last-bar time, strategy heartbeat. Plus existing
agent health (DDE/QUIK alive, link ping). STL LIVE screen / robot window render agent-hosted
robots as a read-only mirror.

## Safety

- Hard limits enforced in BOTH layers: runner (pre-send) and Go `trade.Manager`/`limits.go`
  (existing) — whitelist, per-order qty, max working contracts, price collar, daily cap.
- Dual master flag preserved: real orders require the agent's own `agent_config.json`
  trading flag ON (STL cannot arm it remotely).
- Phantom-order reconciliation (existing, both sides) continues to apply.
- v1 risk caps for the FVG robot: max 1 contract in position.

## Proto changes (`proto/shectory/quik/v1/quik_agent.proto`)

- `AgentMessage` += `RobotStatusReport`.
- `OrchestratorMessage` += `DeployRobot`, `UndeployRobot`, `SetRobotParams`, `PauseRobot`,
  `StartRobot` (KillSwitch exists; clarify robot-scope field).
- NEW local proto (or same file, separate service) for the loopback agent↔runner bridge:
  MarketData, PlaceOrder/CancelOrder, OrderEvents, Control, StatusReport.
- Python stubs regenerated ONLY with `grpcio-tools<1.71` (protobuf 5.29 prod gotcha).

## v1 scope

One robot: FVG (ICT) on RIU6 through QUIK, real money, 1 contract max, hard limits, kill
switch. Framework is N-robot capable; validated on FVG first. Runner paper flag exists for
debugging but default is real (operator decision).

Out of scope for v1: auto-rollover (backlog #9), multi-agent fan-out, strategy hot-reload,
closing positions on kill.

## Testing

- Python: `AgentQuikBroker` unit tests; runner state persistence; limits enforcement
  pre-send; existing strategy tests keep passing.
- Go: runner-bridge tests; command relay; status forwarding; kill-switch cancels working
  orders (extend `reconcile_test.go` patterns).
- E2E on the QUIK VDS: paper-flag dry run against live QUIK data before arming real orders
  (even though rollout target is real, the smoke test uses the paper flag).

## Zero-touch startup (no human factor)

Operator requirement: satellite startup around QUIK must be maximally simple — no start
order to remember, nothing to forget to activate.

- **Single entrypoint.** The Go agent is the ONLY thing that starts. It supervises
  everything else: launches the robot-runner as a child process, restarts it on crash,
  verifies QUIK/DDE/bridge, loads persisted robots. One autostart entry on the VDS
  (Windows service / logon task next to the QUIK terminal autostart) → whole stack up
  after a reboot with zero manual steps.
- **Order-independence.** Every link retries until its dependency appears: agent waits for
  QUIK DDE (already does), runner reconnects to the agent bridge in a loop, agent redials
  STL in a loop (already does). Starting things in any order — or a component dying and
  coming back — converges to the same running state.
- **Idempotent start.** Double-launch is a no-op (single-instance lock). Deployed robots
  auto-resume from local persisted spec+state — no re-deploy from STL needed after reboot.
- **Bundled artifact.** The runner ships as a single self-contained exe (PyInstaller-style)
  published and self-updated ALONGSIDE the agent by `publish_quik_agent.sh` — no Python
  install, venv, or pip steps on the VDS.
- **Startup self-check + traffic light.** On start the agent runs a checklist (QUIK alive,
  DDE alive, Lua bridge ok, runner healthy, robots loaded, master flag state) and reports
  ONE aggregate readiness status locally (log/console) and to STL when connected. The LIVE
  screen shows it; a not-ready component is named explicitly (e.g. "bridge: cmd.jsonl not
  writable") instead of silent degradation.
- The only deliberate manual act that remains is arming the master trading flag in
  `agent_config.json` (by design — a human decision, not a startup step).

## Migration

`live-fvg-RIU6` currently trades the Finam account from STL. Moving it to the agent changes
the execution account to the QUIK terminal's. Cutover: undeploy in STL → deploy to agent →
verify status mirror → operator arms the agent master flag. Do not run both concurrently on
real money.
