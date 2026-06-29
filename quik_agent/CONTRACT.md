# QUIK agent <-> STL contract (sprint02 Phase 1)

Phase 1 is READ-ONLY. No order transactions. See docs/sprint02.md for the source spec.

## Components (sub-agent ownership)
- **A** `quik_agent/` (Go): DDE reader + gRPC client. Reuses PiranhaAI `quikdde` (DDE, go-ole).
- **C** `trader/` (Python): gRPC server (STL side), settings menu "Интерфейс биржи", agent status.
- **D**: resilience: Windows service install, watchdog, diagnostics stream, Telegram alert sink.
- **E** `docs/`: test plan + step-by-step acceptance checklist.

## Wire contract
- Canonical proto: `proto/shectory/quik/v1/quik_agent.proto`. Both sides generate from it.
- Agent DIALS OUT to STL and opens ONE long-lived bidi stream `QuikAgentLink.Session`.
  Windows box stays behind NAT, no inbound port.
- Auth: Bearer in gRPC metadata `authorization: Bearer <token>`. Token from keymaster
  (requester `klod-stl`), never hardcoded. STL verifies like the Showcase link.
- Agent -> STL: `AgentMessage` (Register, Heartbeat, Securities, Tick, OrderBook, Params,
  Diagnostics, Alert). STL -> agent: `OrchestratorMessage` (Ack, Command).

## QUIK transport (reference: PiranhaAI local_agent_go)
- DDE only (read-only): securities reference, last/quote/OI, order book table, params
  (Шаг цены / Ст. шага цены). Source on smain: `~/workspaces/projects/PiranhaAI/local_agent_go/internal/quikdde`.
- Commission coef = step_cost / price_step (taker for backtest reference). See reference_commission_model.

## Self-update (reference: PiranhaAI)
- Single exe. Check on start + daily 03:00 + on `COMMAND_TYPE_SELF_UPDATE`. Compare build_rev,
  download one ZIP for the process arch, restart same exe. Disable via env flag.

## Boundaries (Guard 3 — human-only)
- NO order placement / move / cancel / market trade in Phase 1 code paths.
- SMS gateway (Garden-manager): Phase 1 only proves the integration on one CRITICAL alert.
- Real Bearer token value: never printed/committed; provisioned via keymaster.

## Codegen
- Python: `python -m grpc_tools.protoc -Iproto --python_out=... --grpc_python_out=... proto/shectory/quik/v1/quik_agent.proto`
- Go: `protoc --go_out=. --go-grpc_out=. -Iproto proto/shectory/quik/v1/quik_agent.proto`
