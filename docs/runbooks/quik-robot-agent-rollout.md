# Rollout: live-fvg-RIU6 -> QUIK agent

Spec: `docs/superpowers/specs/2026-07-03-quik-side-robot-agent-design.md`.
Every step below that touches real trading is OPERATOR-GATED (HITL). The agent's
local persisted state is the runtime source of truth; STL commands are optional.

## Build + publish (one-time per release)

1. On WINDOWS (dev box): `bash deploy/build_runner.sh` -> `dist/runner/robot-runner.exe`.
2. Stage it: `scp dist/runner/robot-runner.exe hoster:~/quik_build/quik_agent/dist/`.
3. On the hoster: `bash ~/apps/shectory-trader/deploy/publish_quik_agent.sh [AGENT_ID]`
   — the release zip now carries BOTH exes; agent self-update delivers them together.

## Pre-flight (paper smoke on the QUIK VDS)

1. Agent self-updates (or operator drops both exes next to each other) + restart.
2. Verify console shows, in order:
   - `runner-bridge: runner bridge listening on 127.0.0.1:50071`
   - `robots:  runner=...robot-runner.exe  bridge=127.0.0.1:50071  persisted=0`
   - `runner-sup: runner started pid=...`
   - after ~15s: `ready: quik=ok dde=ok runner=ok robots=0 trading_enabled=false`
3. From STL, deploy PAPER first:
   `POST /api/v1/quik/robots/live-fvg-RIU6/deploy-agent`
   body: `{"strategy_id":"fvg","symbol":"RIU6","schedule":"09:00-23:55",
           "max_position":1,"paper":true,
           "params":{"symbol":"RIU6","qty":1,"avg_max":1,"tp_atr":60,
                     "min_frac":12,"avg_atr_n":5,"avg_step_atr":24}}`
4. Watch `GET /api/v1/quik/agent/{id}/robots`: heartbeat fresh, `last_bar_unix`
   advancing during the session, paper fills appearing on FVG signals.
5. Parity check: compare the paper fills against the STL backtest over the same bars.

## Cutover (operator-gated, real money)

1. STOP the STL-side robot — never both paths live at once:
   `POST /api/v1/robots/live-fvg-RIU6/undeploy` AND set `state_json.live_real=false` (DB).
2. Re-deploy to the agent with `"paper": false, "max_position": 1`.
3. OPERATOR arms the agent master flag (`agent_config.json quik_trading_enabled=true`)
   + QUIK terminal logged in + Lua bridge running. THIS IS THE HUMAN DECISION POINT.
   NOTE: execution account switches to the QUIK terminal's account (was Finam).
4. Verify the first real order lifecycle in the QUIK terminal + the STL mirror.

## Rollback / emergency

- KillSwitch (this agent only): the existing kill route — blocks new orders AND
  cancels working orders; the runner halts all robots. Positions stay open —
  close manually in QUIK if needed. `start-agent` on a robot clears the halt.
- `POST /api/v1/quik/robots/live-fvg-RIU6/undeploy-agent` removes the robot
  (persisted spec deleted; runner stops it).
- Reverse cutover: re-arm the STL-side robot (reverse of step 1).

## Reboot drill (zero-touch)

Reboot the QUIK VDS. Expected with NO manual steps: QUIK autostarts, the agent
(service/logon task) starts, the runner is supervised up, robots resume from
`robots\robots.json` + `robots\runner_state.json` (position/avg/realized/state
restored), the status mirror goes fresh in STL. The ONLY manual act that ever
remains: the master trading flag.

## Verify loops

- Python: `python -m pytest tests/runner/ tests/quik/ -q`
- Go (hoster): `go test ./internal/robots/ ./internal/runner/ ./internal/link/ ./internal/trade/`
