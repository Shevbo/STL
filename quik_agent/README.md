# Shectory QUIK agent (Go, Windows, single binary)

Local agent that reads QUIK market data over **DDE (read-only)** and streams it to
STL over one long-lived bidi gRPC stream. The agent **dials out** to STL, so the
Windows box stays behind NAT with no inbound port.

**Phase 1 is READ-ONLY.** No order placement, move, cancel, or market trade exists
anywhere in this code. The DDE channel is inbound only (QUIK -> agent).

Ported from PiranhaAI `local_agent_go` (the `quikdde` DDE/DDEML reader and the
self-update restart helper). Adapted to module `shectory/quik_agent`.

## Layout

```
quik_agent/
  cmd/quik-agent/main.go        entry: wizard, DDE, link, self-update
  internal/
    config/                     agent_config.json + first-run wizard
    commission/                 coef = step_cost / price_step (+ table tests)
    quikdde/                    DDE reader (Windows) + in-memory Provider views
    link/                       gRPC bidi client (dial out to STL)
    health/                     channel-state machine + Diagnostics/Alert builder (pure logic)
    watchdog/                   DDE-liveness supervisor: read-only restart on staleness
    service/                    Windows service install/uninstall/start/stop + console fallback
    notify/                     LOCAL Telegram fallback (used only when the gRPC link is down)
    selfupdate/                 start + daily 03:00 + COMMAND_TYPE_SELF_UPDATE
    pb/                         GENERATED gRPC stubs (package quikv1) — see codegen
  proto is at ../proto/shectory/quik/v1/quik_agent.proto (shared, not in this dir)
  buf.gen.yaml / gen.bat        codegen
```

## Prerequisites (Windows build box / CI)

- Go 1.22+
- For codegen: `protoc`, `protoc-gen-go`, `protoc-gen-go-grpc` on `PATH`:

```bat
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
```

(Or `buf` instead of protoc, for `buf.gen.yaml`.)

## 1. Codegen (run first; required before build)

The generated stubs in `internal/pb` are **not committed** — generate them:

```bat
cd quik_agent
gen.bat
```

This produces `internal\pb\quik_agent.pb.go` and `internal\pb\quik_agent_grpc.pb.go`
(package `quikv1`, import path `shectory/quik_agent/internal/pb`).

The proto declares `option go_package = "shectory/quik/v1;quikv1"`; `gen.bat`
overrides the import path with `-M…=shectory/quik_agent/internal/pb` and
`--go_opt=module=shectory/quik_agent` so the files land flat in `internal/pb`.

Buf alternative (CI):

```bat
cd quik_agent
buf generate ../proto --path shectory/quik/v1/quik_agent.proto
```

## 2. Build

```bat
cd quik_agent
go mod tidy
rem 64-bit (must match QUIK process bitness)
set GOOS=windows
set GOARCH=amd64
go build -ldflags "-X main.agentBuildRevStr=%BUILD_REV%" -o dist\quik-agent_amd64.exe .\cmd\quik-agent
rem 32-bit
set GOARCH=386
go build -ldflags "-X main.agentBuildRevStr=%BUILD_REV%" -o dist\quik-agent.exe .\cmd\quik-agent
```

`%BUILD_REV%` is a monotonically increasing integer (e.g. CI build number) used by
self-update to compare against the published release. Omit it for a dev build (0).

## 3. Configure the token (never hardcoded)

The Bearer token is provisioned via keymaster (requester `klod-stl`) and exported as
an env var. The config stores only the env var **name**:

```bat
set STL_QUIK_AGENT_TOKEN=<value from keymaster>
```

The default env var name is `STL_QUIK_AGENT_TOKEN`; change it in the wizard if
needed. The agent refuses to start if the named env var is empty.

## 4. Run on the target machine

The agent process bitness must match QUIK. Place the matching exe next to where you
want `agent_config.json`, then run it once interactively to complete the wizard:

```bat
quik-agent_amd64.exe
```

Wizard prompts: STL gRPC URL (host:port), disable TLS (dev only), token env var
name, QUIK data root, market-data flush interval, heartbeat interval. After the
first run, `agent_config.json` is reused.

In QUIK, configure the DDE export to service **`SHECTORY_QUIK`**, topic **`data`**,
with the list name as the item (e.g. `params`). Export the securities/params,
quotes, and order book tables.

## Environment variables

| Var | Effect |
| --- | --- |
| `STL_QUIK_AGENT_TOKEN` (or your `token_env`) | Bearer token value (required) |
| `SHECTORY_AGENT_RELEASE_URL` | base URL for self-update; unset disables self-update |
| `SHECTORY_AGENT_NO_SELFUPDATE=1` | disable self-update |
| `SHECTORY_DISABLE_DDE=1` | do not bring up DDE (link-only) |
| `SHECTORY_DDE_DLL=<path>` | explicit module exporting `Dde*` |
| `SHECTORY_DDE_DEBUG=1` | full DDE trace |
| `SHECTORY_DDE_VERBOSE=1` | per-packet DDE noise |
| `SHECTORY_AGENT_TG_BOT_TOKEN` | NAME of Telegram bot token for the LOCAL link-down fallback (value never hardcoded) |
| `SHECTORY_AGENT_TG_CHAT_ID` | NAME of the Telegram chat id for the LOCAL link-down fallback |

The two `SHECTORY_AGENT_TG_*` vars are read by **name** only. If either is unset the
local notifier is a no-op. They are used **only** as a fallback when the gRPC link
itself is down (so a link outage can still be signalled out-of-band); the normal
alert path is the gRPC `Alert` frame to STL, which fans CRITICAL alerts to Telegram
(and, in Phase 2, SMS). No SMS gateway is contacted in Phase 1; CRITICAL alerts are
only **marked** for the future SMS dub.

Flags: `--config <path>`, `--no-self-update`, `--service install|uninstall|start|stop|run`.

## Tests (no QUIK / no credentials needed)

```bat
cd quik_agent
go test ./internal/commission/... ./internal/quikdde/...
```

`commission` has table-driven coefficient tests; `quikdde` has XlTable decode tests
ported from the Microsoft Excel SDK examples. These build on any OS (the Windows DDE
server is behind a build tag; the stub builds elsewhere).

## Self-update

Mirrors PiranhaAI: checks on start, daily at 03:00 local, and on
`COMMAND_TYPE_SELF_UPDATE` from STL. It compares the running `build_rev` with the
release source, downloads one ZIP for the process arch, stages it, and spawns a
detached `.bat` that waits for exit, copies the new exe over, and restarts the same
exe name. Disable with `SHECTORY_AGENT_NO_SELFUPDATE=1` or `--no-self-update`.

Release endpoints expected at `SHECTORY_AGENT_RELEASE_URL`:

- `GET /agent_release?arch=<amd64|386>` -> decimal build_rev in the body
- `GET /agent_release/zip?arch=<amd64|386>` -> the update ZIP (contains the exe)

## Resilience (Windows service, watchdog, diagnostics)

Phase 1 stays **READ-ONLY** throughout: the service, watchdog, health, and notify
layers only manage the process lifecycle, restart the inbound DDE channel, and
emit diagnostics/alerts. No order is ever placed, moved, or cancelled.

### Run as a Windows service (survives OS reboot)

Run the agent **once interactively first** to complete the wizard and write
`agent_config.json` (the SCM has no console for the wizard). Then, from an
**elevated** (Administrator) prompt:

```bat
rem install as an auto-start service (auto-start => starts on every boot)
quik-agent_amd64.exe --service install

rem optionally pin a config path; it is baked into the service command line
quik-agent_amd64.exe --service install --config C:\SHECTORY\agent_config.json

quik-agent_amd64.exe --service start
quik-agent_amd64.exe --service stop
quik-agent_amd64.exe --service uninstall
```

- Service name: `ShectoryQuikAgent` (display "Shectory QUIK Agent").
- `install` sets **StartType = Automatic**, so the service comes back after an OS
  reboot, and registers an Event Log source.
- When the SCM launches the binary (no `--service` flag, non-interactive session),
  it auto-detects this via `svc.IsWindowsService()` and runs under the SCM;
  otherwise it runs in the foreground (console fallback).

### Reboot / crash survival (restart-on-failure)

`install` also configures **restart-on-failure** via the API
(`SetRecoveryActions`: restart after 5s, 10s, then 30s; failure count reset after
24h). If the API call is unavailable, set it with `sc.exe` from an elevated prompt:

```bat
sc failure ShectoryQuikAgent reset= 86400 actions= restart/5000/restart/10000/restart/30000
sc failureflag ShectoryQuikAgent 1
```

(The spaces after `reset=` and `actions=` are required by `sc.exe`.)

### Watchdog ("иммунитет к зависаниям")

A supervisor goroutine samples DDE liveness and last-tick staleness. If the DDE
server thread dies, or ticks go stale past `dde_down_ms`, it performs a
**read-only DDE restart** (stop + re-`StartDDE`), increments a reconnect counter
(surfaced in `Diagnostics.reconnects_since_start`), and backs off exponentially so
a wedged QUIK is not hammered. It places no orders.

### Health / Diagnostics / Alerts

`internal/health` is a pure state machine. It maps thresholds to
`ChannelState` (UP / DEGRADED / DOWN) for `dde`, `quik`, `link`, builds the
`Diagnostics` frame, and on state **transitions** raises `Alert`s:

| transition | severity | code |
| --- | --- | --- |
| any channel -> DOWN | CRITICAL (SMS-dubbed in Phase 2) | `DDE_DOWN` / `QUIK_DOWN` / `LINK_DOWN` |
| DDE -> DEGRADED | WARN | `DDE_DEGRADED` |
| channel -> UP (recovery) | INFO | `DDE_RECOVERED` / `QUIK_RECOVERED` / `LINK_RECOVERED` |

Alerts and Diagnostics travel over the existing gRPC stream (the heartbeat tick).
When the gRPC link itself is down, `LINK_DOWN` / `LINK_RECOVERED` are sent over the
LOCAL Telegram fallback instead (see the `SHECTORY_AGENT_TG_*` env vars above).

### Thresholds (config, additive — old config files still load)

`agent_config.json` gains four optional fields with sane defaults:

| field | default | meaning |
| --- | --- | --- |
| `stale_tick_ms` | `30000` | DDE -> DEGRADED once the freshest tick is older than this |
| `dde_down_ms` | `60000` | DDE -> DOWN and watchdog restart trigger |
| `heartbeat_sec` | = `heartbeat_interval_sec` | heartbeat / diagnostics / watchdog cadence |
| `diag_interval_sec` | = `heartbeat_sec` | Diagnostics emit cadence |

Absent fields fall back to the defaults, so configs written before this layer load
unchanged.

## Resilience tests

```bat
cd quik_agent
rem pure-logic health state machine (no DDE / no network)
go test ./internal/health/...
```

`internal/health` has table-driven tests for threshold -> ChannelState and
transition -> Alert severity/code. They build on any OS.
