# QUIK-Side Robot Execution Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run LIVE robots (v1: FVG on RIU6, real money, 1 contract) on the QUIK-side Windows agent, fully isolated from STL, with STL as control-plane + read-only monitor.

**Architecture:** The existing Go agent gains a loopback gRPC "runner bridge" and supervises a new bundled Python `robot-runner` child process. The runner reuses `trader/lab/strategies/library.py` 1:1 via an `AgentRuntime` that implements the same `STLRuntime` protocol strategies already consume, but routes orders to the agent's `trade.Manager` → QUIK Lua bridge and builds bars from local QUIK DDE ticks. STL deploys `RobotSpec`s over the existing agent link and receives `RobotStatusReport`s.

**Tech Stack:** Go (agent, `quik_agent/`), Python 3.12 (runner, reuses `trader/`), protobuf/gRPC (existing contract at `proto/shectory/quik/v1/quik_agent.proto`), PyInstaller (runner bundling).

Spec: `docs/superpowers/specs/2026-07-03-quik-side-robot-agent-design.md`

## Global Constraints

- Python proto stubs MUST be regenerated only with `grpcio-tools<1.71` (prod protobuf runtime is 5.29.6; header must read "Protobuf Python Version: 5.29.0"). Verify header before committing.
- Go toolchain is NOT available locally — run `go test`, `make gen`, `make build` on the hoster (`ssh hoster`, PATH already includes `~/go-sdk/go/bin`, `~/go/bin`, `~/protoc/bin`, build tree `~/quik_build`).
- Live trading is human-initiated: never arm master flags, never place real orders, never deploy to the QUIK VDS without explicit operator permission. The agent's `quik_trading_enabled` in `agent_config.json` stays the dual master flag — never pushed remotely.
- Hard limits enforced in BOTH layers (runner pre-send AND Go `trade.Guard`). v1 robot cap: max 1 contract in position.
- KillSwitch semantics: block new + cancel all working orders; positions are LEFT open. Scope = this agent only.
- Zero-touch startup: the Go agent is the single entrypoint; runner is supervised (started/restarted) by the agent; deployed robots auto-resume from local persisted state; double-launch is a no-op.
- All new Go code follows existing patterns in `quik_agent/internal/*` (plain `fmt.Printf` logf callbacks, table-driven tests). Python follows `trader/` patterns (structlog, pytest-asyncio auto mode).
- Commit after every task (small, focused commits). Do not push or deploy — operator does that.

---

### Task 1: Proto — robot-hosting messages on the STL↔agent link

**Files:**
- Modify: `proto/shectory/quik/v1/quik_agent.proto`
- Regenerate: `quik_agent/internal/pb/*` (Go, on hoster via `make gen`), `trader/quik/pb/*` (Python, pinned venv on hoster)

**Interfaces:**
- Consumes: existing `AgentMessage`/`OrchestratorMessage` oneofs, `Side`, `KillSwitch`, `OrderUpdate`.
- Produces (used by Tasks 3-5, 7-9, 11): messages `RobotSpec`, `DeployRobot`, `UndeployRobot`, `SetRobotParams`, `PauseRobot`, `StartRobot`, `RobotFill`, `RobotStatus`, `RobotStatusReport`; new oneof fields `OrchestratorMessage.deploy_robot=10 / undeploy_robot=11 / set_robot_params=12 / pause_robot=13 / start_robot=14` and `AgentMessage.robot_status_report=15`.

- [ ] **Step 1: Append the robot-hosting section to the proto**

Add before the `===== Phase 2: orders & execution =====` banner in `proto/shectory/quik/v1/quik_agent.proto`:

```proto
// ===================== Robot hosting (agent-side execution) =====================
// STL "deploys" a robot's working logic to the agent. The agent persists the spec
// locally and the bundled robot-runner executes it against local QUIK data + the
// QUIK bridge — fully isolated from STL (trades even when STL is down). Local
// state is the runtime source of truth; STL re-pushes config on reconnect and
// receives status reports. Commands are optional and idempotent.

message RobotSpec {
  string robot_id = 1;
  string strategy_id = 2;           // library id, e.g. "fvg" (make_on_bar id)
  string params_json = 3;           // strategy params as JSON (includes symbol, qty)
  string symbol = 4;                // traded instrument, e.g. RIU6
  string schedule = 5;              // trading window "HH:MM-HH:MM" MSK
  int64 max_position_contracts = 6; // hard cap on |position| (v1: 1)
  bool paper = 7;                   // runner debug flag; false = real orders
}

message DeployRobot { RobotSpec spec = 1; }
message UndeployRobot { string robot_id = 1; }
message SetRobotParams { string robot_id = 1; string params_json = 2; }
message PauseRobot { string robot_id = 1; }
message StartRobot { string robot_id = 1; }

message RobotFill {
  string order_id = 1;
  string symbol = 2;
  Side side = 3;
  int64 qty = 4;
  double price = 5;
  string status = 6;                // filled | rejected | skipped | paper
  int64 ts_unix_ms = 7;
}

message RobotStatus {
  string robot_id = 1;
  bool running = 2;                 // scheduled and inside window loop
  bool paused = 3;
  int64 position = 4;               // signed contracts
  double avg_price = 5;
  double realized_pnl = 6;          // rubles, session
  int64 last_bar_unix = 7;          // newest bar the strategy saw
  int64 heartbeat_unix_ms = 8;      // runner's per-robot liveness
  repeated RobotFill recent_fills = 9;   // last <=20
  string note = 10;                 // last log / error line
}

message RobotStatusReport {
  repeated RobotStatus robots = 1;
  int64 sent_at_unix_ms = 2;
  bool runner_healthy = 3;          // the supervised runner process is up + reporting
}
```

Extend the oneofs (exact new fields):

```proto
// in AgentMessage.payload oneof, after limits_state = 14:
    RobotStatusReport robot_status_report = 15; // agent-hosted robot status mirror

// in OrchestratorMessage.payload oneof, after set_limits = 9:
    DeployRobot deploy_robot = 10;       // hand a robot's logic to the agent
    UndeployRobot undeploy_robot = 11;
    SetRobotParams set_robot_params = 12;
    PauseRobot pause_robot = 13;
    StartRobot start_robot = 14;
```

- [ ] **Step 2: Regenerate Go stubs on the hoster and verify build**

```bash
# local: commit the proto first (stub regen reads the pushed file), or scp it:
scp "proto/shectory/quik/v1/quik_agent.proto" hoster:~/quik_build/proto/shectory/quik/v1/
ssh hoster 'cd ~/quik_build && make gen && cd quik_agent && go build ./... && go test ./internal/trade/ -count=1'
```
Expected: `go build` OK, existing trade tests PASS.

- [ ] **Step 3: Regenerate Python stubs with the PINNED toolchain**

```bash
ssh hoster 'source ~/pbpin/bin/activate && cd ~/apps/shectory-trader && \
  python -m grpc_tools.protoc -I proto --python_out=trader/quik/pb --grpc_python_out=trader/quik/pb \
  proto/shectory/quik/v1/quik_agent.proto && \
  head -12 trader/quik/pb/shectory/quik/v1/quik_agent_pb2.py | grep "Protobuf Python Version"'
```
Expected output contains: `Protobuf Python Version: 5.29.0`. If venv `~/pbpin` is missing, create it: `python3 -m venv ~/pbpin && ~/pbpin/bin/pip install "grpcio-tools<1.71" "protobuf==5.29.6"`.
Copy the regenerated pb files back into the local working tree (scp) so they are committed from one place.

- [ ] **Step 4: Verify Python stubs import under the local runtime**

Run: `python -c "from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb; m = pb.RobotSpec(robot_id='x', strategy_id='fvg'); print(m.robot_id)"`
Expected: `x`

- [ ] **Step 5: Commit**

```bash
git add proto/shectory/quik/v1/quik_agent.proto trader/quik/pb quik_agent/internal/pb
git commit -m "feat(proto): robot-hosting messages (RobotSpec deploy/control + RobotStatusReport)"
```

---

### Task 2: Proto — local RunnerBridge service (agent↔runner loopback)

**Files:**
- Create: `proto/shectory/quik/v1/runner_bridge.proto`
- Regenerate: Go + Python stubs (same commands as Task 1)

**Interfaces:**
- Consumes: `MarketDataTick`, `PlaceOrder`, `CancelOrder`, `OrderUpdate`, `KillSwitch`, robot-hosting messages from Task 1 (same package `shectory.quik.v1`, so plain type references).
- Produces (used by Tasks 4, 7): service `RunnerBridge` with rpcs `StreamTicks`, `PlaceOrder`, `CancelOrder`, `StreamOrderEvents`, `StreamControl`, `ReportStatus`; messages `TickFilter`, `EventsFilter`, `ControlHello`, `RunnerControl`, `BridgeAck`.

- [ ] **Step 1: Write the proto**

```proto
syntax = "proto3";

// LOCAL loopback contract between the Go quik-agent and the bundled Python
// robot-runner on the SAME Windows VDS (127.0.0.1 only, no auth, never exposed).
// The agent is the server. The runner consumes ticks + order events, places
// orders (re-checked by the agent's Guard), receives relayed STL control
// commands, and reports robot status (forwarded to STL by the agent).
package shectory.quik.v1;

import "shectory/quik/v1/quik_agent.proto";

option go_package = "shectory/quik/v1;quikv1";

service RunnerBridge {
  rpc StreamTicks(TickFilter) returns (stream MarketDataTick);
  rpc PlaceRunnerOrder(PlaceOrder) returns (BridgeAck);
  rpc CancelRunnerOrder(CancelOrder) returns (BridgeAck);
  rpc StreamOrderEvents(EventsFilter) returns (stream OrderUpdate);
  rpc StreamControl(ControlHello) returns (stream RunnerControl);
  rpc ReportStatus(RobotStatusReport) returns (BridgeAck);
}

message TickFilter { repeated string codes = 1; }         // empty = all whitelisted
message EventsFilter { string client_prefix = 1; }        // runner's client_id prefix, e.g. "rr:"
message ControlHello { string runner_version = 1; int32 pid = 2; }

message RunnerControl {
  oneof payload {
    DeployRobot deploy = 1;
    UndeployRobot undeploy = 2;
    SetRobotParams set_params = 3;
    PauseRobot pause = 4;
    StartRobot start = 5;
    KillSwitch kill = 6;
  }
}

message BridgeAck { bool ok = 1; string error = 2; }
```

- [ ] **Step 2: Regenerate Go + Python stubs (same procedure as Task 1 Steps 2-3), verify header + imports**

Run the same regen commands adding `proto/shectory/quik/v1/runner_bridge.proto`. Expected: Go builds; Python header `5.29.0`; `python -c "from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2_grpc"` imports.

- [ ] **Step 3: Commit**

```bash
git add proto/shectory/quik/v1/runner_bridge.proto trader/quik/pb quik_agent/internal/pb
git commit -m "feat(proto): local RunnerBridge loopback service (agent<->runner)"
```

---

### Task 3: Go — local robot store (persisted RobotSpecs)

**Files:**
- Create: `quik_agent/internal/robots/store.go`
- Test: `quik_agent/internal/robots/store_test.go`

**Interfaces:**
- Consumes: `quikv1.RobotSpec` (Task 1).
- Produces (Tasks 4-6): `robots.NewStore(dir string) (*Store, error)`; methods `Put(spec *quikv1.RobotSpec) error`, `Delete(robotID string) error`, `Get(robotID string) *quikv1.RobotSpec`, `All() []*quikv1.RobotSpec`, `SetPaused(robotID string, paused bool) error`, `Paused(robotID string) bool`. All safe for concurrent use; persisted as one JSON file `robots.json` in `dir`, written atomically (tmp+rename); paused flags persisted alongside specs.

- [ ] **Step 1: Write the failing test**

```go
package robots

import (
	"os"
	"path/filepath"
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

func TestStorePutGetPersistReload(t *testing.T) {
	dir := t.TempDir()
	s, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	spec := &quikv1.RobotSpec{RobotId: "live-fvg-RIU6", StrategyId: "fvg",
		Symbol: "RIU6", Schedule: "09:00-23:55", MaxPositionContracts: 1,
		ParamsJson: `{"symbol":"RIU6","qty":1}`}
	if err := s.Put(spec); err != nil {
		t.Fatal(err)
	}
	if got := s.Get("live-fvg-RIU6"); got == nil || got.StrategyId != "fvg" {
		t.Fatalf("get after put = %+v", got)
	}
	if err := s.SetPaused("live-fvg-RIU6", true); err != nil {
		t.Fatal(err)
	}

	// RELOAD from disk — specs and paused flags must survive (zero-touch resume).
	s2, err := NewStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got := s2.Get("live-fvg-RIU6"); got == nil || got.Symbol != "RIU6" {
		t.Fatalf("reload lost spec: %+v", got)
	}
	if !s2.Paused("live-fvg-RIU6") {
		t.Fatal("reload lost paused flag")
	}
	if n := len(s2.All()); n != 1 {
		t.Fatalf("All() = %d, want 1", n)
	}

	if err := s2.Delete("live-fvg-RIU6"); err != nil {
		t.Fatal(err)
	}
	if s2.Get("live-fvg-RIU6") != nil {
		t.Fatal("delete did not remove spec")
	}
	// file must exist and be valid JSON after every mutation
	if _, err := os.Stat(filepath.Join(dir, "robots.json")); err != nil {
		t.Fatal("robots.json missing after mutations")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh hoster 'cd ~/quik_build/quik_agent && go test ./internal/robots/ -count=1'`
Expected: FAIL — package does not exist / `NewStore` undefined.

- [ ] **Step 3: Write the implementation**

```go
// Package robots persists agent-hosted RobotSpecs locally so deployed robots
// auto-resume after an agent/VDS restart without STL (zero-touch startup).
package robots

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"

	"google.golang.org/protobuf/encoding/protojson"

	quikv1 "shectory/quik_agent/internal/pb"
)

type entry struct {
	Spec   json.RawMessage `json:"spec"`   // protojson-encoded RobotSpec
	Paused bool            `json:"paused"`
}

type Store struct {
	mu     sync.Mutex
	path   string
	specs  map[string]*quikv1.RobotSpec
	paused map[string]bool
}

func NewStore(dir string) (*Store, error) {
	s := &Store{
		path:   filepath.Join(dir, "robots.json"),
		specs:  map[string]*quikv1.RobotSpec{},
		paused: map[string]bool{},
	}
	raw, err := os.ReadFile(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return s, nil
		}
		return nil, err
	}
	var m map[string]entry
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	for id, e := range m {
		spec := &quikv1.RobotSpec{}
		if err := protojson.Unmarshal(e.Spec, spec); err != nil {
			continue // skip a corrupt entry, never fail startup
		}
		s.specs[id] = spec
		s.paused[id] = e.Paused
	}
	return s, nil
}

func (s *Store) flushLocked() error {
	m := map[string]entry{}
	for id, spec := range s.specs {
		b, err := protojson.Marshal(spec)
		if err != nil {
			return err
		}
		m[id] = entry{Spec: b, Paused: s.paused[id]}
	}
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.path) // atomic on the same volume
}

func (s *Store) Put(spec *quikv1.RobotSpec) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.specs[spec.GetRobotId()] = spec
	return s.flushLocked()
}

func (s *Store) Delete(robotID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.specs, robotID)
	delete(s.paused, robotID)
	return s.flushLocked()
}

func (s *Store) Get(robotID string) *quikv1.RobotSpec {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.specs[robotID]
}

func (s *Store) All() []*quikv1.RobotSpec {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]*quikv1.RobotSpec, 0, len(s.specs))
	for _, sp := range s.specs {
		out = append(out, sp)
	}
	return out
}

func (s *Store) SetPaused(robotID string, paused bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.paused[robotID] = paused
	return s.flushLocked()
}

func (s *Store) Paused(robotID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.paused[robotID]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ssh hoster 'cd ~/quik_build/quik_agent && go test ./internal/robots/ -count=1 -v'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quik_agent/internal/robots/
git commit -m "feat(agent): persisted local robot store (zero-touch resume)"
```

---

### Task 4: Go — RunnerBridge gRPC server

**Files:**
- Create: `quik_agent/internal/runner/server.go`
- Test: `quik_agent/internal/runner/server_test.go`

**Interfaces:**
- Consumes: `quikv1.RunnerBridgeServer` (Task 2 stubs), `robots.Store` (Task 3), tick source `TickSource` (satisfied by `quikdde.Default`), order sink `OrderSink` (satisfied by `*trade.Manager`), status sink `StatusSink` (satisfied by the link).
- Produces (Tasks 5-6, 7): `runner.NewServer(cfg ServerCfg) *Server` where

```go
type TickSource interface { Snapshot() []*quikv1.MarketDataTick } // poll-based, 250ms
type OrderSink interface {
	PlaceOrder(*quikv1.PlaceOrder)
	CancelOrder(*quikv1.CancelOrder)
	KillSwitch(*quikv1.KillSwitch)
}
type StatusSink interface { ForwardRobotStatus(*quikv1.RobotStatusReport) }
type ServerCfg struct {
	Store  *robots.Store
	Ticks  TickSource
	Orders OrderSink
	Status StatusSink
	Logf   func(string, ...any)
}
func NewServer(cfg ServerCfg) *Server
func (s *Server) Serve(ctx context.Context, addr string) error   // addr "127.0.0.1:50071"
func (s *Server) PushControl(rc *quikv1.RunnerControl)            // relay STL command to runner
func (s *Server) FanOrderEvent(u *quikv1.OrderUpdate)             // manager events -> runner stream
func (s *Server) RunnerHealthy() bool                             // control stream open + recent status
```
Order events are fanned ONLY for `client_id` with the runner's prefix (`rr:`), so human-initiated STL orders stay invisible to the runner. `ReportStatus` timestamps runner liveness and forwards to STL.

- [ ] **Step 1: Write the failing test**

```go
package runner

import (
	"context"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

type fakeOrders struct{ placed []*quikv1.PlaceOrder; killed int }

func (f *fakeOrders) PlaceOrder(p *quikv1.PlaceOrder)   { f.placed = append(f.placed, p) }
func (f *fakeOrders) CancelOrder(*quikv1.CancelOrder)   {}
func (f *fakeOrders) KillSwitch(*quikv1.KillSwitch)     { f.killed++ }

type fakeStatus struct{ got []*quikv1.RobotStatusReport }

func (f *fakeStatus) ForwardRobotStatus(r *quikv1.RobotStatusReport) { f.got = append(f.got, r) }

type fakeTicks struct{}

func (fakeTicks) Snapshot() []*quikv1.MarketDataTick {
	return []*quikv1.MarketDataTick{{Code: "RIU6", Last: 89000, ReceivedAtUnixMs: time.Now().UnixMilli()}}
}

func startTestServer(t *testing.T) (*Server, quikv1.RunnerBridgeClient, *fakeOrders, *fakeStatus) {
	t.Helper()
	st, _ := robots.NewStore(t.TempDir())
	fo, fs := &fakeOrders{}, &fakeStatus{}
	srv := NewServer(ServerCfg{Store: st, Ticks: fakeTicks{}, Orders: fo, Status: fs,
		Logf: func(string, ...any) {}})
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go srv.serveListener(ctx, lis)
	conn, err := grpc.NewClient(lis.Addr().String(),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return srv, quikv1.NewRunnerBridgeClient(conn), fo, fs
}

func TestPlaceOrderReachesSinkAndStatusForwards(t *testing.T) {
	srv, cli, fo, fs := startTestServer(t)
	ctx := context.Background()

	ack, err := cli.PlaceRunnerOrder(ctx, &quikv1.PlaceOrder{
		ClientId: "rr:live-fvg-RIU6:1", Code: "RIU6", Side: quikv1.Side_SIDE_BUY,
		Price: 89000, Quantity: 1})
	if err != nil || !ack.GetOk() {
		t.Fatalf("place: err=%v ack=%+v", err, ack)
	}
	if len(fo.placed) != 1 || fo.placed[0].GetCode() != "RIU6" {
		t.Fatalf("sink placed = %+v", fo.placed)
	}

	_, err = cli.ReportStatus(ctx, &quikv1.RobotStatusReport{
		Robots: []*quikv1.RobotStatus{{RobotId: "live-fvg-RIU6", Running: true}}})
	if err != nil {
		t.Fatal(err)
	}
	if len(fs.got) != 1 {
		t.Fatalf("status not forwarded: %d", len(fs.got))
	}
	if !srv.RunnerHealthy() {
		t.Fatal("runner must be healthy right after a status report")
	}
}

func TestControlRelayAndOrderEventFan(t *testing.T) {
	srv, cli, _, _ := startTestServer(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	ctrl, err := cli.StreamControl(ctx, &quikv1.ControlHello{RunnerVersion: "test"})
	if err != nil {
		t.Fatal(err)
	}
	// Give the stream a moment to register, then push a deploy.
	time.Sleep(100 * time.Millisecond)
	srv.PushControl(&quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Deploy{
		Deploy: &quikv1.DeployRobot{Spec: &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg"}}}})
	rc, err := ctrl.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if rc.GetDeploy().GetSpec().GetRobotId() != "r1" {
		t.Fatalf("control relay got %+v", rc)
	}

	ev, err := cli.StreamOrderEvents(ctx, &quikv1.EventsFilter{ClientPrefix: "rr:"})
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(100 * time.Millisecond)
	srv.FanOrderEvent(&quikv1.OrderUpdate{ClientId: "human-1", Code: "GZU6"}) // must be filtered out
	srv.FanOrderEvent(&quikv1.OrderUpdate{ClientId: "rr:r1:2", Code: "RIU6",
		State: quikv1.OrderState_ORDER_STATE_FILLED})
	u, err := ev.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if u.GetClientId() != "rr:r1:2" {
		t.Fatalf("event fan leaked/missed: %+v", u)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ssh hoster 'cd ~/quik_build/quik_agent && go test ./internal/runner/ -count=1'`
Expected: FAIL — package/NewServer undefined.

- [ ] **Step 3: Write the implementation**

```go
// Package runner hosts the loopback gRPC bridge the bundled Python robot-runner
// connects to: ticks out, orders in (re-checked by the agent Guard downstream),
// STL control relayed, status reports forwarded to STL. 127.0.0.1 only.
package runner

import (
	"context"
	"net"
	"strings"
	"sync"
	"time"

	"google.golang.org/grpc"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

type TickSource interface{ Snapshot() []*quikv1.MarketDataTick }

type OrderSink interface {
	PlaceOrder(*quikv1.PlaceOrder)
	CancelOrder(*quikv1.CancelOrder)
	KillSwitch(*quikv1.KillSwitch)
}

type StatusSink interface{ ForwardRobotStatus(*quikv1.RobotStatusReport) }

type ServerCfg struct {
	Store  *robots.Store
	Ticks  TickSource
	Orders OrderSink
	Status StatusSink
	Logf   func(string, ...any)
}

const (
	tickPollInterval = 250 * time.Millisecond
	healthyWithin    = 90 * time.Second // runner counts healthy if it reported recently
	runnerPrefix     = "rr:"
)

type Server struct {
	quikv1.UnimplementedRunnerBridgeServer
	cfg ServerCfg

	mu          sync.Mutex
	ctrlCh      chan *quikv1.RunnerControl // nil when no runner control stream
	eventSubs   []chan *quikv1.OrderUpdate
	lastReport  time.Time
	ctrlAttached bool
}

func NewServer(cfg ServerCfg) *Server {
	if cfg.Logf == nil {
		cfg.Logf = func(string, ...any) {}
	}
	return &Server{cfg: cfg}
}

func (s *Server) Serve(ctx context.Context, addr string) error {
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return s.serveListener(ctx, lis)
}

func (s *Server) serveListener(ctx context.Context, lis net.Listener) error {
	gs := grpc.NewServer()
	quikv1.RegisterRunnerBridgeServer(gs, s)
	go func() { <-ctx.Done(); gs.GracefulStop() }()
	s.cfg.Logf("runner bridge listening on %s", lis.Addr())
	return gs.Serve(lis)
}

// ---- runner -> agent ----

func (s *Server) PlaceRunnerOrder(_ context.Context, p *quikv1.PlaceOrder) (*quikv1.BridgeAck, error) {
	if !strings.HasPrefix(p.GetClientId(), runnerPrefix) {
		return &quikv1.BridgeAck{Ok: false, Error: "client_id must be prefixed rr:"}, nil
	}
	s.cfg.Orders.PlaceOrder(p)
	return &quikv1.BridgeAck{Ok: true}, nil
}

func (s *Server) CancelRunnerOrder(_ context.Context, c *quikv1.CancelOrder) (*quikv1.BridgeAck, error) {
	s.cfg.Orders.CancelOrder(c)
	return &quikv1.BridgeAck{Ok: true}, nil
}

func (s *Server) ReportStatus(_ context.Context, r *quikv1.RobotStatusReport) (*quikv1.BridgeAck, error) {
	s.mu.Lock()
	s.lastReport = time.Now()
	s.mu.Unlock()
	r.RunnerHealthy = true
	s.cfg.Status.ForwardRobotStatus(r)
	return &quikv1.BridgeAck{Ok: true}, nil
}

// ---- agent -> runner streams ----

func (s *Server) StreamTicks(f *quikv1.TickFilter, stream quikv1.RunnerBridge_StreamTicksServer) error {
	want := map[string]bool{}
	for _, c := range f.GetCodes() {
		want[c] = true
	}
	t := time.NewTicker(tickPollInterval)
	defer t.Stop()
	sent := map[string]int64{} // code -> last received_at we already pushed
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case <-t.C:
			for _, tick := range s.cfg.Ticks.Snapshot() {
				if len(want) > 0 && !want[tick.GetCode()] {
					continue
				}
				if sent[tick.GetCode()] >= tick.GetReceivedAtUnixMs() {
					continue // unchanged since last push
				}
				sent[tick.GetCode()] = tick.GetReceivedAtUnixMs()
				if err := stream.Send(tick); err != nil {
					return err
				}
			}
		}
	}
}

func (s *Server) StreamOrderEvents(f *quikv1.EventsFilter, stream quikv1.RunnerBridge_StreamOrderEventsServer) error {
	ch := make(chan *quikv1.OrderUpdate, 256)
	s.mu.Lock()
	s.eventSubs = append(s.eventSubs, ch)
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		for i, c := range s.eventSubs {
			if c == ch {
				s.eventSubs = append(s.eventSubs[:i], s.eventSubs[i+1:]...)
				break
			}
		}
		s.mu.Unlock()
	}()
	prefix := f.GetClientPrefix()
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case u := <-ch:
			if prefix != "" && !strings.HasPrefix(u.GetClientId(), prefix) {
				continue
			}
			if err := stream.Send(u); err != nil {
				return err
			}
		}
	}
}

func (s *Server) StreamControl(h *quikv1.ControlHello, stream quikv1.RunnerBridge_StreamControlServer) error {
	ch := make(chan *quikv1.RunnerControl, 64)
	s.mu.Lock()
	s.ctrlCh = ch
	s.ctrlAttached = true
	s.mu.Unlock()
	s.cfg.Logf("runner connected (version=%s pid=%d)", h.GetRunnerVersion(), h.GetPid())
	// Zero-touch resume: replay every persisted spec as a Deploy on (re)connect.
	for _, spec := range s.cfg.Store.All() {
		ch <- &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Deploy{
			Deploy: &quikv1.DeployRobot{Spec: spec}}}
		if s.cfg.Store.Paused(spec.GetRobotId()) {
			ch <- &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Pause{
				Pause: &quikv1.PauseRobot{RobotId: spec.GetRobotId()}}}
		}
	}
	defer func() {
		s.mu.Lock()
		if s.ctrlCh == ch {
			s.ctrlCh = nil
			s.ctrlAttached = false
		}
		s.mu.Unlock()
	}()
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case rc := <-ch:
			if err := stream.Send(rc); err != nil {
				return err
			}
		}
	}
}

// ---- agent-side entry points ----

// PushControl relays an STL command to the connected runner (drops when absent —
// commands are optional; persisted state is replayed on the next connect anyway).
func (s *Server) PushControl(rc *quikv1.RunnerControl) {
	s.mu.Lock()
	ch := s.ctrlCh
	s.mu.Unlock()
	if ch == nil {
		return
	}
	select {
	case ch <- rc:
	default:
		s.cfg.Logf("runner control channel full; dropping command")
	}
}

// FanOrderEvent forwards a manager order event to runner subscribers.
func (s *Server) FanOrderEvent(u *quikv1.OrderUpdate) {
	s.mu.Lock()
	subs := append([]chan *quikv1.OrderUpdate(nil), s.eventSubs...)
	s.mu.Unlock()
	for _, ch := range subs {
		select {
		case ch <- u:
		default: // slow consumer: drop rather than block the trade path
		}
	}
}

func (s *Server) RunnerHealthy() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ctrlAttached && time.Since(s.lastReport) < healthyWithin
}
```

- [ ] **Step 4: Run tests**

Run: `ssh hoster 'cd ~/quik_build/quik_agent && go test ./internal/runner/ -count=1 -v'`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add quik_agent/internal/runner/
git commit -m "feat(agent): RunnerBridge loopback gRPC server (ticks/orders/control/status)"
```

---

### Task 5: Go — link handling of robot commands + status forwarding

**Files:**
- Modify: `quik_agent/internal/link/stream.go` (recvLoop switch), `quik_agent/internal/link/link.go` (Options + setter)
- Modify: `quik_agent/internal/trade/manager.go` — fan order events to the runner bridge
- Test: `quik_agent/internal/link/robots_relay_test.go`

**Interfaces:**
- Consumes: `runner.Server` (Task 4: `PushControl`), `robots.Store` (Task 3), new pb messages (Task 1).
- Produces: link `Options` gains `Robots *robots.Store` and `Runner interface{ PushControl(*quikv1.RunnerControl) }`; link method `ForwardRobotStatus(r *quikv1.RobotStatusReport)` (satisfies `runner.StatusSink`) that sends `AgentMessage{RobotStatusReport}` on the active stream (drops silently when no session — isolation). `trade.Manager` emitter path additionally calls `runnerFan(u *quikv1.OrderUpdate)` when set via `mgr.SetRunnerFan(func(*quikv1.OrderUpdate))`.

- [ ] **Step 1: Write the failing test** (`internal/link/robots_relay_test.go`)

```go
package link

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/robots"
)

type fakeRunner struct{ got []*quikv1.RunnerControl }

func (f *fakeRunner) PushControl(rc *quikv1.RunnerControl) { f.got = append(f.got, rc) }

func TestHandleRobotCommandsPersistAndRelay(t *testing.T) {
	st, _ := robots.NewStore(t.TempDir())
	fr := &fakeRunner{}
	l := New(Options{Robots: st, Runner: fr})

	spec := &quikv1.RobotSpec{RobotId: "r1", StrategyId: "fvg", Symbol: "RIU6"}
	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_DeployRobot{
		DeployRobot: &quikv1.DeployRobot{Spec: spec}}})
	if st.Get("r1") == nil {
		t.Fatal("deploy must persist the spec")
	}
	if len(fr.got) != 1 || fr.got[0].GetDeploy() == nil {
		t.Fatalf("deploy must relay to runner, got %+v", fr.got)
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_PauseRobot{
		PauseRobot: &quikv1.PauseRobot{RobotId: "r1"}}})
	if !st.Paused("r1") {
		t.Fatal("pause must persist")
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_SetRobotParams{
		SetRobotParams: &quikv1.SetRobotParams{RobotId: "r1", ParamsJson: `{"qty":1}`}}})
	if st.Get("r1").GetParamsJson() != `{"qty":1}` {
		t.Fatal("set_params must update the persisted spec")
	}

	l.handleRobotMsg(&quikv1.OrchestratorMessage{Payload: &quikv1.OrchestratorMessage_UndeployRobot{
		UndeployRobot: &quikv1.UndeployRobot{RobotId: "r1"}}})
	if st.Get("r1") != nil {
		t.Fatal("undeploy must delete the spec")
	}
	if len(fr.got) != 4 {
		t.Fatalf("all 4 commands must relay, got %d", len(fr.got))
	}
}
```

- [ ] **Step 2: Run to verify FAIL** (`handleRobotMsg` undefined; `Options.Robots` unknown).

- [ ] **Step 3: Implement**

In `link.go` add to `Options`:

```go
	// Robot hosting: persisted specs + the runner bridge to relay control into.
	Robots *robots.Store
	Runner interface{ PushControl(*quikv1.RunnerControl) }
```

In `stream.go` extend the recvLoop switch:

```go
		case *quikv1.OrchestratorMessage_DeployRobot,
			*quikv1.OrchestratorMessage_UndeployRobot,
			*quikv1.OrchestratorMessage_SetRobotParams,
			*quikv1.OrchestratorMessage_PauseRobot,
			*quikv1.OrchestratorMessage_StartRobot:
			l.handleRobotMsg(msg)
```

New method (same file):

```go
// handleRobotMsg persists a robot command into the local store (source of truth
// for zero-touch resume) and relays it to the runner when one is attached.
func (l *Link) handleRobotMsg(msg *quikv1.OrchestratorMessage) {
	if l.opt.Robots == nil {
		return
	}
	var rc *quikv1.RunnerControl
	switch p := msg.GetPayload().(type) {
	case *quikv1.OrchestratorMessage_DeployRobot:
		_ = l.opt.Robots.Put(p.DeployRobot.GetSpec())
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Deploy{Deploy: p.DeployRobot}}
	case *quikv1.OrchestratorMessage_UndeployRobot:
		_ = l.opt.Robots.Delete(p.UndeployRobot.GetRobotId())
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Undeploy{Undeploy: p.UndeployRobot}}
	case *quikv1.OrchestratorMessage_SetRobotParams:
		if spec := l.opt.Robots.Get(p.SetRobotParams.GetRobotId()); spec != nil {
			spec.ParamsJson = p.SetRobotParams.GetParamsJson()
			_ = l.opt.Robots.Put(spec)
		}
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_SetParams{SetParams: p.SetRobotParams}}
	case *quikv1.OrchestratorMessage_PauseRobot:
		_ = l.opt.Robots.SetPaused(p.PauseRobot.GetRobotId(), true)
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Pause{Pause: p.PauseRobot}}
	case *quikv1.OrchestratorMessage_StartRobot:
		_ = l.opt.Robots.SetPaused(p.StartRobot.GetRobotId(), false)
		rc = &quikv1.RunnerControl{Payload: &quikv1.RunnerControl_Start{Start: p.StartRobot}}
	default:
		return
	}
	if l.opt.Runner != nil && rc != nil {
		l.opt.Runner.PushControl(rc)
	}
}

// ForwardRobotStatus sends an agent-hosted robot status report to STL. Satisfies
// runner.StatusSink. Dropped silently when no session is open (isolation: the
// runner never blocks on STL availability).
func (l *Link) ForwardRobotStatus(r *quikv1.RobotStatusReport) {
	stream := l.currentStream()
	if stream == nil {
		return
	}
	_ = l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_RobotStatusReport{RobotStatusReport: r},
	})
}
```

`currentStream()` — add a small accessor holding the active stream in the Link struct (set on session open, cleared on close), mutex-guarded; follow the existing pattern used for `l.subs`.

Also relay KillSwitch to the runner: in the existing `OrchestratorMessage_KillSwitch` case add:

```go
			if l.opt.Runner != nil {
				l.opt.Runner.PushControl(&quikv1.RunnerControl{
					Payload: &quikv1.RunnerControl_Kill{Kill: p.KillSwitch}})
			}
```

In `trade/manager.go`: where OrderUpdate frames are emitted (the single helper that calls `emit.EmitOrderUpdate(u)`), add a runner fan callback:

```go
// SetRunnerFan registers a callback receiving every OrderUpdate (used to fan
// events to the local robot-runner). Optional; nil-safe.
func (m *Manager) SetRunnerFan(f func(*quikv1.OrderUpdate)) { m.runnerFan = f }
```
with `runnerFan func(*quikv1.OrderUpdate)` field, invoked right after each successful `EmitOrderUpdate` call: `if m.runnerFan != nil { m.runnerFan(u) }`.

- [ ] **Step 4: Run tests**

Run: `ssh hoster 'cd ~/quik_build/quik_agent && go test ./... -count=1'`
Expected: all packages PASS (link, runner, robots, trade).

- [ ] **Step 5: Commit**

```bash
git add quik_agent/internal/link/ quik_agent/internal/trade/manager.go
git commit -m "feat(agent): relay robot deploy/control commands + forward status to STL"
```

---

### Task 6: Go — runner supervisor + zero-touch wiring in main.go

**Files:**
- Create: `quik_agent/internal/runner/supervisor.go`
- Test: `quik_agent/internal/runner/supervisor_test.go`
- Modify: `quik_agent/cmd/quik-agent/main.go`, `quik_agent/internal/config/config.go`

**Interfaces:**
- Consumes: `runner.Server` (Task 4), `config.Config`.
- Produces: `runner.NewSupervisor(cfg SupervisorCfg) *Supervisor`; `(s *Supervisor) Run(ctx context.Context)` — starts `RunnerExe` as a child with args `--bridge 127.0.0.1:<port> --data <dir>`, restarts on exit with exponential backoff (1s..60s), logs transitions. Config gains `RunnerExe string` (default `robot-runner.exe` next to the agent exe; empty = robot hosting disabled), `RunnerBridgePort int` (default 50071), `RobotsDataDir string` (default `<exeDir>\robots`). Startup self-check line printed once ready: `ready: quik=<ok|down> dde=<ok|down> bridge=<ok|down> runner=<ok|down|disabled> robots=<n>`.

- [ ] **Step 1: Write the failing test** — restart-with-backoff using a fake command that exits immediately:

```go
package runner

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

func TestSupervisorRestartsAndBacksOff(t *testing.T) {
	var starts int32
	s := NewSupervisor(SupervisorCfg{
		Start: func(ctx context.Context) error { // test seam instead of exec.Command
			atomic.AddInt32(&starts, 1)
			return nil // child "exited" instantly
		},
		BackoffMin: 10 * time.Millisecond,
		BackoffMax: 40 * time.Millisecond,
		Logf:       func(string, ...any) {},
	})
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	s.Run(ctx)
	n := atomic.LoadInt32(&starts)
	if n < 3 || n > 20 {
		t.Fatalf("starts = %d, want a handful with backoff", n)
	}
}
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement**

```go
package runner

import (
	"context"
	"os/exec"
	"time"
)

type SupervisorCfg struct {
	// Start launches the runner and blocks until it exits. Injected for tests;
	// production uses ExecStart below.
	Start      func(ctx context.Context) error
	BackoffMin time.Duration
	BackoffMax time.Duration
	Logf       func(string, ...any)
}

type Supervisor struct{ cfg SupervisorCfg }

func NewSupervisor(cfg SupervisorCfg) *Supervisor {
	if cfg.BackoffMin == 0 {
		cfg.BackoffMin = time.Second
	}
	if cfg.BackoffMax == 0 {
		cfg.BackoffMax = 60 * time.Second
	}
	if cfg.Logf == nil {
		cfg.Logf = func(string, ...any) {}
	}
	return &Supervisor{cfg: cfg}
}

// Run supervises the runner until ctx is done: start, wait for exit, back off,
// restart. Backoff resets after a run that survived >60s.
func (s *Supervisor) Run(ctx context.Context) {
	backoff := s.cfg.BackoffMin
	for ctx.Err() == nil {
		started := time.Now()
		if err := s.cfg.Start(ctx); err != nil && ctx.Err() == nil {
			s.cfg.Logf("runner exited: %v", err)
		}
		if ctx.Err() != nil {
			return
		}
		if time.Since(started) > time.Minute {
			backoff = s.cfg.BackoffMin // healthy run — reset
		}
		s.cfg.Logf("restarting runner in %s", backoff)
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff *= 2; backoff > s.cfg.BackoffMax {
			backoff = s.cfg.BackoffMax
		}
	}
}

// ExecStart returns a production Start func launching exe with args, wiring
// stdout/stderr into logf lines prefixed "runner:".
func ExecStart(exe string, args []string, logf func(string, ...any)) func(ctx context.Context) error {
	return func(ctx context.Context) error {
		cmd := exec.CommandContext(ctx, exe, args...)
		out, _ := cmd.StdoutPipe()
		errp, _ := cmd.StderrPipe()
		go pipeLines(out, logf)
		go pipeLines(errp, logf)
		if err := cmd.Start(); err != nil {
			return err
		}
		logf("runner started pid=%d", cmd.Process.Pid)
		return cmd.Wait()
	}
}
```
(`pipeLines` = bufio.Scanner loop calling `logf("runner: %s", line)`.)

**main.go wiring** (after the trade manager block, before `lk.Run`):

```go
	// ---- Robot hosting: local store + runner bridge + supervised runner ----
	robotStore, rsErr := robots.NewStore(cfg.RobotsDataDir(opt.exeDir))
	if rsErr != nil {
		fmt.Println("robots: store error:", rsErr)
	}
	var runnerSrv *runner.Server
	if robotStore != nil {
		runnerSrv = runner.NewServer(runner.ServerCfg{
			Store: robotStore, Ticks: quikdde.Default, Orders: mgr, Status: lk,
			Logf: func(f string, a ...any) { fmt.Printf("runner-bridge: "+f+"\n", a...) },
		})
		mgr.SetRunnerFan(runnerSrv.FanOrderEvent)
		lk.SetRobots(robotStore, runnerSrv) // sets Options.Robots/Runner equivalents
		go func() {
			addr := fmt.Sprintf("127.0.0.1:%d", cfg.RunnerBridgePort)
			if err := runnerSrv.Serve(ctx, addr); err != nil && ctx.Err() == nil {
				fmt.Println("runner-bridge:", err)
			}
		}()
		if exe := cfg.RunnerExePath(opt.exeDir); exe != "" {
			sup := runner.NewSupervisor(runner.SupervisorCfg{
				Start: runner.ExecStart(exe, []string{
					"--bridge", fmt.Sprintf("127.0.0.1:%d", cfg.RunnerBridgePort),
					"--data", cfg.RobotsDataDir(opt.exeDir),
				}, func(f string, a ...any) { fmt.Printf(f+"\n", a...) }),
				Logf: func(f string, a ...any) { fmt.Printf("runner-sup: "+f+"\n", a...) },
			})
			go sup.Run(ctx)
		} else {
			fmt.Println("runner: robot-runner.exe not found — robot hosting disabled")
		}
		// Startup self-check traffic light (zero-touch: one aggregate line).
		go func() {
			time.Sleep(10 * time.Second)
			ok := func(b bool) string { if b { return "ok" }; return "DOWN" }
			fmt.Printf("ready: quik=%s dde=%s runner=%s robots=%d trading_enabled=%v\n",
				ok(quikdde.Alive()), ok(quikdde.Default.FreshnessMs() >= 0),
				ok(runnerSrv.RunnerHealthy()), len(robotStore.All()), cfg.QuikTradingEnabled)
		}()
	}
```

`trade.Manager` must satisfy `runner.OrderSink` — its existing `PlaceOrder/CancelOrder/KillSwitch` methods already take the pb types, so it does. If `quikdde.Default` does not already expose `Snapshot() []*quikv1.MarketDataTick`, add a thin adapter `internal/runner/ticksource.go` that converts the provider's actual tick accessor into `Snapshot()` — see how `internal/link/marketdata.go` (`flushMarketData`) reads ticks from the provider and mirror that read path; do NOT invent a second tick pipeline. `lk` must satisfy `runner.StatusSink` via `ForwardRobotStatus` (Task 5). Config additions in `config.go` with the same JSON/default pattern as existing keys:

```go
	RunnerExe        string `json:"runner_exe"`         // "" = <exeDir>\robot-runner.exe if present
	RunnerBridgePort int    `json:"runner_bridge_port"` // default 50071
	RobotsDataSubdir string `json:"robots_data_subdir"` // default "robots"
```
plus helpers `RunnerExePath(exeDir string) string` (explicit path, else exeDir join default name, "" when file missing) and `RobotsDataDir(exeDir string) string` (mkdir-all, return path).

- [ ] **Step 4: Run all Go tests + build**

Run: `ssh hoster 'cd ~/quik_build/quik_agent && go build ./... && go test ./... -count=1'`
Expected: build OK, all PASS.

- [ ] **Step 5: Commit**

```bash
git add quik_agent/
git commit -m "feat(agent): supervised robot-runner + zero-touch startup wiring + self-check line"
```

---

### Task 7: Python runner — bridge client + 1-min bar builder

**Files:**
- Create: `robot_runner/__init__.py` (empty), `robot_runner/bridge_client.py`, `robot_runner/bars.py`
- Test: `tests/runner/test_bars.py`, `tests/runner/test_bridge_client.py`

**Interfaces:**
- Consumes: `trader.quik.pb.shectory.quik.v1.runner_bridge_pb2(_grpc)` and `quik_agent_pb2` (Tasks 1-2), `trader.lab.runtime.Bar`.
- Produces (Tasks 8-9):

```python
class BarBuilder:                       # robot_runner/bars.py
    def __init__(self, max_bars: int = 3000) -> None: ...
    def on_tick(self, ts_ms: int, last: float) -> None      # aggregates into 1-min bars
    def bars(self, n: int = 0) -> list[Bar]                  # closed bars only, oldest..newest
    @property
    def last_bar_time(self) -> int                           # 0 when empty

class BridgeClient:                     # robot_runner/bridge_client.py
    def __init__(self, addr: str) -> None: ...               # "127.0.0.1:50071"
    async def start(self) -> None                            # opens channel
    async def ticks(self, codes: list[str]) -> AsyncIterator[pb.MarketDataTick]
    async def order_events(self, prefix: str) -> AsyncIterator[pb.OrderUpdate]
    async def control(self, version: str) -> AsyncIterator[rb.RunnerControl]
    async def place_order(self, *, client_id, code, side, price, qty, collar) -> None  # raises on ack.ok=False
    async def cancel_order(self, client_id: str, order_id: str) -> None
    async def report_status(self, report: pb.RobotStatusReport) -> None
    async def aclose(self) -> None
```
All stream methods auto-reconnect with 1..30s backoff (order-independence: the runner may start before the agent).

- [ ] **Step 1: Write the failing bar-builder test**

```python
from robot_runner.bars import BarBuilder


def test_bar_builder_aggregates_minutes():
    b = BarBuilder()
    t0 = 1_751_500_000_000  # arbitrary ms epoch, minute-aligned enough for bucketing
    m = 60_000
    # minute 0: three ticks
    b.on_tick(t0, 100.0); b.on_tick(t0 + 10_000, 105.0); b.on_tick(t0 + 50_000, 99.0)
    # minute 1 opens -> minute 0 closes
    b.on_tick(t0 + m, 101.0)
    bars = b.bars()
    assert len(bars) == 1
    bar = bars[0]
    assert bar.time == (t0 // m) * 60  # unix seconds, minute-truncated
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 105.0, 99.0, 99.0)
    # forming minute is NOT visible
    assert b.bars(1)[-1].time == bar.time


def test_bar_builder_gap_fills_nothing_and_caps():
    b = BarBuilder(max_bars=2)
    t0 = 1_751_500_000_000
    m = 60_000
    for i in range(4):
        b.on_tick(t0 + i * 2 * m, 100.0 + i)  # every other minute (gaps)
    assert len(b.bars()) == 2  # capped, no synthetic gap bars
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python -m pytest tests/runner/test_bars.py -q`
Expected: FAIL — `robot_runner.bars` not found.

- [ ] **Step 3: Implement `robot_runner/bars.py`**

```python
"""1-minute bar aggregation from local QUIK DDE ticks.

Strategies consume CLOSED bars only (the forming minute is invisible) so a
signal computed mid-minute cannot repaint — matching backtest semantics where
on_bar sees completed candles.
"""

from collections import deque

from trader.lab.runtime import Bar


class BarBuilder:
    def __init__(self, max_bars: int = 3000) -> None:
        self._bars: deque[Bar] = deque(maxlen=max_bars)
        self._cur: Bar | None = None   # forming minute

    def on_tick(self, ts_ms: int, last: float) -> None:
        if last <= 0:
            return
        minute = int(ts_ms // 60_000) * 60  # unix seconds
        cur = self._cur
        if cur is None or minute > cur.time:
            if cur is not None:
                self._bars.append(cur)   # close the previous minute
            self._cur = Bar(time=minute, open=last, high=last, low=last,
                            close=last, volume=0)
            return
        if minute < cur.time:
            return  # late tick from a closed minute — ignore
        cur.high = max(cur.high, last)
        cur.low = min(cur.low, last)
        cur.close = last

    def bars(self, n: int = 0) -> list[Bar]:
        out = list(self._bars)
        return out[-n:] if n else out

    @property
    def last_bar_time(self) -> int:
        return self._bars[-1].time if self._bars else 0
```

- [ ] **Step 4: Run bar tests — PASS expected.**

- [ ] **Step 5: Implement `robot_runner/bridge_client.py`**

```python
"""Async gRPC client for the agent's loopback RunnerBridge.

Every stream reconnects forever with backoff — the runner may start before the
agent (zero-touch: order-independent startup) and must survive agent restarts.
"""

import asyncio
from collections.abc import AsyncIterator

import grpc
import structlog

from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2 as rb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2_grpc as rbg

log = structlog.get_logger()

_BACKOFF_MAX = 30.0


class BridgeClient:
    def __init__(self, addr: str) -> None:
        self._addr = addr
        self._channel: grpc.aio.Channel | None = None
        self._stub: rbg.RunnerBridgeStub | None = None

    async def start(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._addr)
        self._stub = rbg.RunnerBridgeStub(self._channel)

    async def _stream(self, open_stream, name: str) -> AsyncIterator:
        backoff = 1.0
        while True:
            try:
                async for item in open_stream():
                    backoff = 1.0
                    yield item
            except (grpc.aio.AioRpcError, ConnectionError) as exc:
                log.warning("bridge.stream_down", stream=name, error=str(exc))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    def ticks(self, codes: list[str]) -> AsyncIterator[pb.MarketDataTick]:
        return self._stream(
            lambda: self._stub.StreamTicks(rb.TickFilter(codes=codes)), "ticks")

    def order_events(self, prefix: str) -> AsyncIterator[pb.OrderUpdate]:
        return self._stream(
            lambda: self._stub.StreamOrderEvents(rb.EventsFilter(client_prefix=prefix)),
            "order_events")

    def control(self, version: str) -> AsyncIterator[rb.RunnerControl]:
        import os
        return self._stream(
            lambda: self._stub.StreamControl(rb.ControlHello(runner_version=version,
                                                             pid=os.getpid())),
            "control")

    async def place_order(self, *, client_id: str, code: str, side: str,
                          price: float, qty: int, collar: float = 0.002) -> None:
        pb_side = pb.SIDE_BUY if side == "buy" else pb.SIDE_SELL
        ack = await self._stub.PlaceRunnerOrder(pb.PlaceOrder(
            client_id=client_id, code=code, side=pb_side,
            price=price, quantity=qty, collar=collar))
        if not ack.ok:
            raise RuntimeError(f"bridge rejected order: {ack.error}")

    async def cancel_order(self, client_id: str, order_id: str) -> None:
        await self._stub.CancelRunnerOrder(
            pb.CancelOrder(client_id=client_id, order_id=order_id))

    async def report_status(self, report: pb.RobotStatusReport) -> None:
        try:
            await self._stub.ReportStatus(report)
        except (grpc.aio.AioRpcError, ConnectionError) as exc:
            log.warning("bridge.report_failed", error=str(exc))  # never crash on status

    async def aclose(self) -> None:
        if self._channel is not None:
            await self._channel.close()
```

- [ ] **Step 6: Write + run the bridge client test** (`tests/runner/test_bridge_client.py`) — a python `grpc.aio` in-process server implementing `RunnerBridgeServicer` with a scripted `PlaceRunnerOrder` (ok=True then ok=False), assert `place_order` raises on ok=False and passes side mapping:

```python
import pytest
import grpc

from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2 as rb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2_grpc as rbg
from robot_runner.bridge_client import BridgeClient


class _Svc(rbg.RunnerBridgeServicer):
    def __init__(self):
        self.placed = []

    async def PlaceRunnerOrder(self, request, context):
        self.placed.append(request)
        ok = request.code != "REJECTME"
        return rb.BridgeAck(ok=ok, error="" if ok else "guard says no")


@pytest.mark.asyncio
async def test_place_order_maps_and_raises():
    svc = _Svc()
    server = grpc.aio.server()
    rbg.add_RunnerBridgeServicer_to_server(svc, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        cli = BridgeClient(f"127.0.0.1:{port}")
        await cli.start()
        await cli.place_order(client_id="rr:r1:1", code="RIU6", side="buy",
                              price=89000, qty=1)
        assert svc.placed[0].side == pb.SIDE_BUY
        assert svc.placed[0].quantity == 1
        with pytest.raises(RuntimeError):
            await cli.place_order(client_id="rr:r1:2", code="REJECTME", side="sell",
                                  price=1, qty=1)
        await cli.aclose()
    finally:
        await server.stop(None)
```

Run: `python -m pytest tests/runner/ -q` — Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add robot_runner/ tests/runner/
git commit -m "feat(runner): bridge client (reconnecting streams) + 1-min bar builder"
```

---

### Task 8: Python runner — AgentRuntime (STLRuntime protocol) with local position + limits

**Files:**
- Create: `robot_runner/runtime.py`
- Test: `tests/runner/test_agent_runtime.py`

**Interfaces:**
- Consumes: `BridgeClient`, `BarBuilder` (Task 7); `trader.lab.runtime.Bar/Order`; `trader.pos.models.Position/AccountSummary`.
- Produces (Task 9): `AgentRuntime` implementing the `STLRuntime` protocol strategies consume (`get_bars/get_quote/get_orderbook/place_order/cancel_order/get_orders/get_position/get_account/get_state/set_state/log`) plus:

```python
class AgentRuntime:
    def __init__(self, robot_id: str, bridge: BridgeClient, bars: BarBuilder, *,
                 max_position: int = 1, paper: bool = False,
                 state: dict | None = None, fills_log=None) -> None: ...
    def on_order_event(self, u) -> None      # feed OrderUpdate: fills mutate position
    def signed_position(self) -> int
    def recent_fills(self) -> list[dict]      # last <=20 for RobotStatus
    def realized_pnl(self) -> float
    state: dict                                # exposed for persistence by the host
```
Position/avg/realized-P&L bookkeeping uses the SAME signed-space algorithm as `BacktestRuntime.place_order` (`trader/lab/runtime.py:135-158`). Limits pre-send: an order that would push `|signed_position + delta| > max_position` is refused locally (status `skipped`, never sent) — first enforcement line before the Go Guard.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from robot_runner.bars import BarBuilder
from robot_runner.runtime import AgentRuntime


class FakeBridge:
    def __init__(self):
        self.placed = []

    async def place_order(self, **kw):
        self.placed.append(kw)


def _rt(max_position=1):
    return AgentRuntime("r1", FakeBridge(), BarBuilder(), max_position=max_position)


@pytest.mark.asyncio
async def test_place_order_sends_and_position_updates_on_fill():
    rt = _rt()
    order = await rt.place_order("RIU6", "buy", 1, 89000.0)
    assert order.status == "submitted"
    assert len(rt._bridge.placed) == 1
    assert rt._bridge.placed[0]["code"] == "RIU6"
    assert rt.signed_position() == 0  # not filled yet

    # simulate the fill event coming back from the agent
    class U:  # minimal OrderUpdate stand-in (duck-typed accessors)
        client_id = order.order_id
        order_id = "12345"
        code = "RIU6"
        state = 4  # ORDER_STATE_FILLED
        price = 89000.0
        quantity = 1
        filled = 1
        text = ""
        ts_unix_ms = 0
    rt.on_order_event(U())
    assert rt.signed_position() == 1
    assert rt.recent_fills()[-1]["status"] == "filled"


@pytest.mark.asyncio
async def test_max_position_pre_send_guard():
    rt = _rt(max_position=1)
    await rt.place_order("RIU6", "buy", 1, 89000.0)
    # pretend it filled
    rt._apply_fill("buy", 1, 89000.0)
    order = await rt.place_order("RIU6", "buy", 1, 89100.0)  # would make +2
    assert order.status == "skipped"
    assert len(rt._bridge.placed) == 1  # second order NEVER reached the bridge
    # closing/reducing IS allowed at the cap
    order2 = await rt.place_order("RIU6", "sell", 1, 89200.0)
    assert order2.status == "submitted"


@pytest.mark.asyncio
async def test_realized_pnl_signed_space():
    rt = _rt(max_position=2)
    rt._apply_fill("buy", 1, 100.0)
    rt._apply_fill("sell", 1, 110.0)
    assert rt.realized_pnl() == pytest.approx(10.0)
    assert rt.signed_position() == 0
```

- [ ] **Step 2: Run to verify FAIL** (`robot_runner.runtime` missing).

- [ ] **Step 3: Implement `robot_runner/runtime.py`**

```python
"""AgentRuntime — the STLRuntime protocol backed by the local agent bridge.

Strategies from trader/lab/strategies/library.py run against this runtime
UNCHANGED (same protocol LiveRuntime implements in STL). Differences:
 - bars come from local QUIK DDE ticks (BarBuilder), not ISS;
 - orders go to the agent's trade.Manager -> QUIK Lua bridge;
 - position/P&L are tracked locally from OrderUpdate fills (source of truth
   on this box; STL only mirrors it via RobotStatusReport).

Limits: max_position is enforced BEFORE sending (first line; the Go Guard is
the second). An order that would exceed it returns status='skipped'.
"""

import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog

from trader.lab.runtime import Bar, Order
from trader.pos.models import AccountSummary, Position

log = structlog.get_logger()

_FILLED_STATE = 4    # quik_agent.proto OrderState.ORDER_STATE_FILLED
_PARTIAL_STATE = 3


class AgentRuntime:
    def __init__(self, robot_id: str, bridge, bars, *, max_position: int = 1,
                 paper: bool = False, state: dict | None = None,
                 fills_log=None) -> None:
        self._robot_id = robot_id
        self._bridge = bridge
        self._bars = bars
        self._max_position = max(1, int(max_position))
        self._paper = paper
        self._state: dict[str, Any] = dict(state or {})
        self._fills_log = fills_log          # optional callable(dict) for persistence
        self._signed = 0
        self._avg = 0.0
        self._realized = 0.0
        self._fills: list[dict] = []
        self._seq = 0
        self._orders: dict[str, Order] = {}  # client_id -> last known state

    # ---- STLRuntime protocol ----

    async def get_bars(self, symbol: str, tf: int, n: int) -> list[Bar]:
        return self._bars.bars(n)

    async def get_quote(self, symbol: str) -> Any:
        bars = self._bars.bars(1)
        c = bars[-1].close if bars else 0.0
        return {"bid": c, "ask": c, "last": c}

    async def get_orderbook(self, symbol: str) -> Any:
        return {"bids": [], "asks": []}

    async def place_order(self, symbol: str, side: str, qty: int, price: float) -> Order:
        delta = qty if side == "buy" else -qty
        # Reducing is always allowed; growing beyond max_position is refused.
        grows = abs(self._signed + delta) > abs(self._signed)
        if grows and abs(self._signed + delta) > self._max_position:
            self.log(f"SKIP {side} {qty} {symbol}: would exceed max_position="
                     f"{self._max_position} (now {self._signed})", level="warning")
            return Order(order_id="skipped-maxpos", symbol=symbol, side=side,
                         qty=qty, price=price, status="skipped")
        self._seq += 1
        client_id = f"rr:{self._robot_id}:{self._seq}:{uuid4().hex[:6]}"
        if self._paper:
            self._apply_fill(side, qty, price)
            self._record(client_id, symbol, side, qty, price, "paper")
            return Order(order_id=client_id, symbol=symbol, side=side, qty=qty,
                         price=price, status="paper", fill_price=price)
        try:
            await self._bridge.place_order(client_id=client_id, code=symbol,
                                           side=side, price=price, qty=qty)
        except Exception as exc:
            self.log(f"order rejected pre-QUIK: {exc}", level="error")
            self._record(client_id, symbol, side, qty, price, "rejected")
            return Order(order_id=client_id, symbol=symbol, side=side, qty=qty,
                         price=price, status="rejected")
        order = Order(order_id=client_id, symbol=symbol, side=side, qty=qty,
                      price=price, status="submitted")
        self._orders[client_id] = order
        return order

    async def cancel_order(self, order_id: str) -> None:
        await self._bridge.cancel_order(order_id, "")

    async def get_orders(self) -> list[Order]:
        return [o for o in self._orders.values()
                if o.status in ("submitted", "active", "partial")]

    async def get_position(self, symbol: str) -> Position:
        side = "long" if self._signed > 0 else ("short" if self._signed < 0 else "flat")
        return Position(symbol=symbol, account_id="agent", side=side,
                        quantity=abs(self._signed), avg_price=Decimal(str(self._avg)),
                        current_price=Decimal("0"), var_margin=Decimal("0"))

    async def get_account(self) -> AccountSummary:
        return AccountSummary(deposit=Decimal("0"), free=Decimal("0"),
                              in_position=Decimal("0"), variation_margin=Decimal("0"))

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

    def log(self, msg: str, level: str = "info") -> None:
        log.msg(msg, robot_id=self._robot_id, level=level)

    # ---- fills / position bookkeeping ----

    def on_order_event(self, u) -> None:
        """Feed an OrderUpdate from the agent. Fill states mutate the position."""
        cid = getattr(u, "client_id", "")
        if not cid.startswith(f"rr:{self._robot_id}:"):
            return
        state = getattr(u, "state", 0)
        order = self._orders.get(cid)
        if state in (_FILLED_STATE, _PARTIAL_STATE):
            qty = int(getattr(u, "filled", 0)) or int(getattr(u, "quantity", 0))
            side = order.side if order else ("buy" if getattr(u, "side", 1) == 1 else "sell")
            price = float(getattr(u, "price", 0.0))
            already = sum(f["qty"] for f in self._fills if f.get("client_id") == cid)
            fresh = max(0, qty - already)
            if fresh:
                self._apply_fill(side, fresh, price, client_id=cid,
                                 order_id=getattr(u, "order_id", ""))
        if order is not None:
            status = {2: "active", 3: "partial", 4: "filled",
                      5: "cancelled", 6: "rejected"}.get(state, order.status)
            self._orders[cid] = Order(order_id=cid, symbol=order.symbol,
                                      side=order.side, qty=order.qty,
                                      price=order.price, status=status)

    def _apply_fill(self, side: str, qty: int, price: float, *,
                    client_id: str = "", order_id: str = "") -> None:
        # Same signed-space algorithm as BacktestRuntime.place_order.
        delta = qty if side == "buy" else -qty
        signed, avg = self._signed, self._avg
        if signed != 0 and (signed > 0) != (delta > 0):
            closed = min(qty, abs(signed))
            self._realized += ((price - avg) if signed > 0 else (avg - price)) * closed
        new_signed = signed + delta
        if new_signed == 0:
            self._signed, self._avg = 0, 0.0
        elif signed != 0 and (signed > 0) == (delta > 0):
            total = abs(signed) + qty
            self._avg = (avg * abs(signed) + price * qty) / total
            self._signed = new_signed
        else:
            self._signed, self._avg = new_signed, price
        self._record(client_id or "fill", "", side, qty, price, "filled",
                     order_id=order_id)

    def _record(self, client_id, symbol, side, qty, price, status, order_id="") -> None:
        f = {"client_id": client_id, "order_id": order_id or client_id,
             "symbol": symbol, "side": side, "qty": qty, "price": price,
             "status": status, "ts_ms": int(time.time() * 1000)}
        self._fills.append(f)
        if len(self._fills) > 200:
            self._fills = self._fills[-200:]
        if self._fills_log:
            try:
                self._fills_log(f)
            except Exception:
                pass

    def signed_position(self) -> int:
        return self._signed

    def recent_fills(self) -> list[dict]:
        return self._fills[-20:]

    def realized_pnl(self) -> float:
        return self._realized

    @property
    def state(self) -> dict:
        return self._state
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/runner/test_agent_runtime.py -q`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add robot_runner/runtime.py tests/runner/test_agent_runtime.py
git commit -m "feat(runner): AgentRuntime — STLRuntime protocol on the local bridge, pre-send max_position guard"
```

---

### Task 9: Python runner — robot host loop (schedule, control, status, persistence)

**Files:**
- Create: `robot_runner/host.py`, `robot_runner/main.py`
- Test: `tests/runner/test_host.py`

**Interfaces:**
- Consumes: `BridgeClient`, `BarBuilder`, `AgentRuntime` (Tasks 7-8); `trader.lab.strategies.library.make_on_bar(rid)`; window helpers reimported from `trader.lab.scheduler` (`_parse_window`, `_within_window`, `_MSK`).
- Produces: `RobotHost` managing N robots:

```python
class HostedRobot:      # one deployed robot
    spec: dict          # {robot_id, strategy_id, params(dict), symbol, schedule, max_position, paper}
    runtime: AgentRuntime
    paused: bool

class RobotHost:
    def __init__(self, bridge: BridgeClient, data_dir: str) -> None: ...
    async def run(self) -> None                     # main loop: control + ticks + minute scheduler + status
    async def handle_control(self, rc) -> None      # deploy/undeploy/set_params/pause/start/kill
    def status_report(self) -> pb.RobotStatusReport
    # persistence: <data_dir>/runner_state.json — {robot_id: {"state": {...}, "position": int, "avg": float, "realized": float}}
    # written atomically after every on_bar tick and every fill.
```
`robot_runner/main.py` — argparse (`--bridge`, `--data`), builds BridgeClient + RobotHost, `asyncio.run(host.run())`. KillSwitch control: sets `killed=True` on the host — all robots pause (no new on_bar orders); active order cancellation is done agent-side by the existing `Manager.KillSwitch`.

- [ ] **Step 1: Write the failing test**

```python
import json

import pytest

from robot_runner.host import RobotHost


class FakeBridge:
    def __init__(self):
        self.placed = []
        self.reports = []

    async def place_order(self, **kw):
        self.placed.append(kw)

    async def report_status(self, r):
        self.reports.append(r)


def _deploy_rc(robot_id="r1", strategy="fvg", paused=False):
    """Duck-typed RunnerControl stand-in for handle_control."""
    class Spec:
        pass
    spec = Spec()
    spec.robot_id = robot_id
    spec.strategy_id = strategy
    spec.params_json = json.dumps({"symbol": "RIU6", "qty": 1, "min_frac": 12,
                                   "tp_atr": 60, "avg_max": 1, "avg_atr_n": 5,
                                   "avg_step_atr": 24})
    spec.symbol = "RIU6"
    spec.schedule = "00:00-23:59"
    spec.max_position_contracts = 1
    spec.paper = True

    class Deploy:
        pass
    d = Deploy()
    d.spec = spec

    class RC:
        deploy = d
        def WhichOneof(self, _):
            return "deploy"
        def HasField(self, f):
            return f == "deploy"
    rc = RC()
    return rc


@pytest.mark.asyncio
async def test_deploy_creates_robot_and_persists(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    assert "r1" in host.robots
    assert host.robots["r1"].spec["strategy_id"] == "fvg"
    # persisted runner state file exists after deploy
    assert (tmp_path / "runner_state.json").exists()

    # a second host instance resumes the robot's saved runtime state
    host.robots["r1"].runtime.set_state("trend", "up")
    host.persist()
    host2 = RobotHost(FakeBridge(), str(tmp_path))
    await host2.handle_control(_deploy_rc())
    assert host2.robots["r1"].runtime.get_state("trend") == "up"


@pytest.mark.asyncio
async def test_tick_runs_strategy_on_new_closed_bar(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    t0 = 1_751_500_000_000
    # feed enough 1-min bars for FVG (needs >= 4 bars closed)
    for i in range(6):
        price = 89_000 + i * 30
        r.bars.on_tick(t0 + i * 60_000, price)
    # closing tick for the last minute
    r.bars.on_tick(t0 + 6 * 60_000, 89_200)
    ran = await host.tick_robot(r)   # returns True when on_bar executed
    assert ran is True
    assert r.last_bar_run == r.bars.last_bar_time
    # same bar -> no rerun (one on_bar per closed bar, backtest parity)
    assert await host.tick_robot(r) is False


@pytest.mark.asyncio
async def test_kill_switch_pauses_everything(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())

    class Kill:
        reason = "test"
    class RC:
        kill = Kill()
        def WhichOneof(self, _):
            return "kill"
    await host.handle_control(RC())
    assert host.killed is True
    assert await host.tick_robot(host.robots["r1"]) is False
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement `robot_runner/host.py`**

```python
"""RobotHost — schedules deployed robots, relays control, reports status.

One on_bar per CLOSED 1-min bar per robot (backtest parity: the strategy sees
the same bar cadence the backtester replays). Local persistence is the source
of truth: specs live in the agent's robots.json (replayed as Deploy on every
control-stream connect); runtime state (strategy state dict, position, avg,
realized P&L) lives in <data_dir>/runner_state.json, written atomically.
"""

import asyncio
import json
import os
import time
from datetime import datetime

import structlog

from trader.lab.scheduler import _MSK, _parse_window, _within_window
from trader.lab.strategies.library import make_on_bar
from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb

from robot_runner.bars import BarBuilder
from robot_runner.runtime import AgentRuntime

log = structlog.get_logger()

STATUS_INTERVAL_S = 15.0


class HostedRobot:
    def __init__(self, spec: dict, runtime: AgentRuntime, bars: BarBuilder) -> None:
        self.spec = spec
        self.runtime = runtime
        self.bars = bars
        self.paused = False
        self.last_bar_run = 0        # newest closed-bar time already executed
        self.on_bar = make_on_bar(spec["strategy_id"])
        self.window = _parse_window(spec.get("schedule"))
        self.last_error = ""


class RobotHost:
    def __init__(self, bridge, data_dir: str) -> None:
        self._bridge = bridge
        self._data_dir = data_dir
        self._state_path = os.path.join(data_dir, "runner_state.json")
        self.robots: dict[str, HostedRobot] = {}
        self.killed = False
        self._saved = self._load()

    # ---- persistence ----

    def _load(self) -> dict:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def persist(self) -> None:
        out = {}
        for rid, r in self.robots.items():
            out[rid] = {"state": r.runtime.state,
                        "position": r.runtime.signed_position(),
                        "avg": r.runtime._avg,
                        "realized": r.runtime.realized_pnl()}
        tmp = self._state_path + ".tmp"
        os.makedirs(self._data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        os.replace(tmp, self._state_path)

    # ---- control ----

    async def handle_control(self, rc) -> None:
        kind = rc.WhichOneof("payload") if hasattr(rc, "WhichOneof") else None
        if kind == "deploy":
            spec_pb = rc.deploy.spec
            spec = {
                "robot_id": spec_pb.robot_id,
                "strategy_id": spec_pb.strategy_id,
                "params": json.loads(spec_pb.params_json or "{}"),
                "symbol": spec_pb.symbol,
                "schedule": spec_pb.schedule,
                "max_position": int(spec_pb.max_position_contracts or 1),
                "paper": bool(spec_pb.paper),
            }
            saved = self._saved.get(spec["robot_id"], {})
            bars = self.robots[spec["robot_id"]].bars \
                if spec["robot_id"] in self.robots else BarBuilder()
            rt = AgentRuntime(spec["robot_id"], self._bridge, bars,
                              max_position=spec["max_position"],
                              paper=spec["paper"], state=saved.get("state"))
            rt._signed = int(saved.get("position", 0))
            rt._avg = float(saved.get("avg", 0.0))
            rt._realized = float(saved.get("realized", 0.0))
            self.robots[spec["robot_id"]] = HostedRobot(spec, rt, bars)
            log.info("host.deployed", robot_id=spec["robot_id"],
                     strategy=spec["strategy_id"], paper=spec["paper"])
            self.persist()
        elif kind == "undeploy":
            self.robots.pop(rc.undeploy.robot_id, None)
            self.persist()
        elif kind == "set_params":
            r = self.robots.get(rc.set_params.robot_id)
            if r is not None:
                r.spec["params"] = json.loads(rc.set_params.params_json or "{}")
        elif kind == "pause":
            r = self.robots.get(rc.pause.robot_id)
            if r is not None:
                r.paused = True
        elif kind == "start":
            r = self.robots.get(rc.start.robot_id)
            if r is not None:
                r.paused = False
            self.killed = False   # an explicit start clears a kill
        elif kind == "kill":
            self.killed = True    # block all new orders; agent cancels working ones
            log.warning("host.kill_switch", reason=getattr(rc.kill, "reason", ""))

    # ---- scheduling ----

    async def tick_robot(self, r: HostedRobot) -> bool:
        """Run on_bar once if there is a NEW closed bar, inside the window, not
        paused/killed. Returns True when the strategy executed."""
        if self.killed or r.paused:
            return False
        if not _within_window(datetime.now(_MSK), *r.window):
            return False
        last = r.bars.last_bar_time
        if last == 0 or last == r.last_bar_run:
            return False
        try:
            await r.on_bar(r.runtime, r.spec["params"])
            r.last_error = ""
        except Exception as exc:
            r.last_error = str(exc)
            log.error("host.on_bar_failed", robot_id=r.spec["robot_id"], error=str(exc))
        r.last_bar_run = last
        self.persist()
        return True

    def status_report(self) -> pb.RobotStatusReport:
        robots = []
        for rid, r in self.robots.items():
            fills = [pb.RobotFill(order_id=f["order_id"], symbol=f["symbol"] or r.spec["symbol"],
                                  side=pb.SIDE_BUY if f["side"] == "buy" else pb.SIDE_SELL,
                                  qty=f["qty"], price=f["price"], status=f["status"],
                                  ts_unix_ms=f["ts_ms"]) for f in r.runtime.recent_fills()]
            robots.append(pb.RobotStatus(
                robot_id=rid, running=not (self.killed or r.paused), paused=r.paused,
                position=r.runtime.signed_position(), avg_price=r.runtime._avg,
                realized_pnl=r.runtime.realized_pnl(), last_bar_unix=r.bars.last_bar_time,
                heartbeat_unix_ms=int(time.time() * 1000),
                recent_fills=fills, note=r.last_error))
        return pb.RobotStatusReport(robots=robots, sent_at_unix_ms=int(time.time() * 1000))

    # ---- main loop ----

    async def run(self) -> None:
        codes = lambda: sorted({r.spec["symbol"] for r in self.robots.values()})  # noqa: E731

        async def consume_control():
            async for rc in self._bridge.control("robot-runner/1"):
                await self.handle_control(rc)

        async def consume_ticks():
            async for t in self._bridge.ticks([]):
                for r in self.robots.values():
                    if r.spec["symbol"] == t.code:
                        r.bars.on_tick(t.received_at_unix_ms, t.last)

        async def consume_events():
            async for u in self._bridge.order_events("rr:"):
                for r in self.robots.values():
                    r.runtime.on_order_event(u)
                self.persist()

        async def schedule():
            while True:
                for r in list(self.robots.values()):
                    await self.tick_robot(r)
                await asyncio.sleep(1.0)   # cheap check; on_bar gated by new-closed-bar

        async def report():
            while True:
                await self._bridge.report_status(self.status_report())
                await asyncio.sleep(STATUS_INTERVAL_S)

        await asyncio.gather(consume_control(), consume_ticks(),
                             consume_events(), schedule(), report())
```

`robot_runner/main.py`:

```python
"""robot-runner entrypoint. Supervised by the quik-agent; do not run two copies."""

import argparse
import asyncio

from robot_runner.bridge_client import BridgeClient
from robot_runner.host import RobotHost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="127.0.0.1:50071")
    ap.add_argument("--data", default="robots")
    args = ap.parse_args()

    async def amain():
        bridge = BridgeClient(args.bridge)
        await bridge.start()
        host = RobotHost(bridge, args.data)
        await host.run()

    asyncio.run(amain())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/runner/ -q`
Expected: all runner tests PASS. Also run the full suite: `python -m pytest -m "not integration" -q` — no regressions.

- [ ] **Step 5: Commit**

```bash
git add robot_runner/ tests/runner/
git commit -m "feat(runner): RobotHost — schedule/control/status/persistence + entrypoint"
```

---

### Task 10: Bundling + publish + config

**Files:**
- Create: `robot_runner/build.spec` (PyInstaller), `deploy/build_runner.sh`
- Modify: `deploy/publish_quik_agent.sh` (ship runner exe alongside agent), `quik_agent/internal/config/config.go` docs comment

**Interfaces:**
- Consumes: `robot_runner/main.py` (Task 9).
- Produces: `robot-runner.exe` (onefile, Windows amd64) placed next to `quik-agent.exe`; the agent's `RunnerExePath` finds it automatically (Task 6) — zero-touch.

- [ ] **Step 1: PyInstaller spec** (`robot_runner/build.spec`)

```python
# PyInstaller spec: single-exe robot-runner for the QUIK VDS (no Python install).
# Build (Windows or wine/hoster cross tooling): pyinstaller robot_runner/build.spec
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['robot_runner/main.py'],
    pathex=['.'],
    hiddenimports=(collect_submodules('trader.lab.strategies')
                   + collect_submodules('trader.quik.pb')
                   + ['grpc', 'structlog']),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas,
          name='robot-runner', console=True, onefile=True)
```

- [ ] **Step 2: Build script** (`deploy/build_runner.sh`) — runs ON A WINDOWS BOX (the runner targets Windows; PyInstaller does not cross-compile). Document that constraint in the header. Local dev machine (this repo's Windows host) is the builder:

```bash
#!/usr/bin/env bash
# Build robot-runner.exe (Windows). RUN ON WINDOWS (PyInstaller can't cross-build).
set -euo pipefail
python -m pip show pyinstaller >/dev/null || python -m pip install pyinstaller
python -m PyInstaller --clean -y robot_runner/build.spec --distpath dist/runner
echo "built: dist/runner/robot-runner.exe"
```

- [ ] **Step 3: Extend `deploy/publish_quik_agent.sh`** — after the agent exe is staged into the release dir, also copy `dist/runner/robot-runner.exe` when present (uploaded to the hoster release dir by the operator/CI beforehand) and include it in the release manifest so the agent's self-update pulls BOTH exes. Keep `build_rev` numeric-epoch rule.

- [ ] **Step 4: Smoke test locally (paper flag, no QUIK)** — run the runner as a plain Python process against a stub: `python -m robot_runner.main --bridge 127.0.0.1:59999 --data C:\Temp\claude\...\rr-test` — expected: starts, logs reconnect backoff (no agent listening), Ctrl+C exits cleanly. This validates the zero-touch order-independence claim.

- [ ] **Step 5: Commit**

```bash
git add robot_runner/build.spec deploy/build_runner.sh deploy/publish_quik_agent.sh
git commit -m "build(runner): PyInstaller onefile + publish alongside agent (self-update ships both)"
```

---

### Task 11: STL — deploy/undeploy API + status mirror

**Files:**
- Modify: `trader/quik/server.py` (handle `robot_status_report`), `trader/quik/store.py` (hold last report per agent), `trader/api/app.py` (routes)
- Test: `tests/quik/test_robot_hosting.py`

**Interfaces:**
- Consumes: pb messages (Task 1), existing `QuikAgentServer` enqueue mechanism (same path SetLimits/PlaceOrder use), `QuikAgentStore` lock pattern.
- Produces:
  - `QuikAgentStore.set_robot_report(agent_id: str, report) / robot_report(agent_id) -> dict | None` (stored as plain dict with `received_at_ms`).
  - `POST /api/v1/quik/robots/{robot_id}/deploy-agent` body `{agent_id, strategy_id, params, symbol, schedule, max_position, paper}` → enqueues `OrchestratorMessage{deploy_robot}`; also `.../undeploy-agent`, `.../pause-agent`, `.../start-agent`, `POST /api/v1/quik/agent/{agent_id}/robots/kill`.
  - `GET /api/v1/quik/agent/{agent_id}/robots` → last `RobotStatusReport` as JSON for the LIVE screen.
- All routes `_auth`-guarded, enqueue-only (agent may be offline; command is optional by design — document in route docstrings that persisted agent state is the source of truth).

- [ ] **Step 1: Write the failing test** (store round-trip + server dispatch):

```python
from trader.quik.store import QuikAgentStore


def test_robot_report_roundtrip():
    st = QuikAgentStore()
    st.set_robot_report("9618", {"robots": [{"robot_id": "r1", "position": 1}],
                                 "sent_at_unix_ms": 123})
    rep = st.robot_report("9618")
    assert rep["robots"][0]["robot_id"] == "r1"
    assert "received_at_ms" in rep
    assert st.robot_report("nope") is None
```

- [ ] **Step 2: Run to verify FAIL, then implement** — `set_robot_report/robot_report` with the store's existing `self._lock` pattern; in `server.py`'s AgentMessage dispatch add a `robot_status_report` case converting via `google.protobuf.json_format.MessageToDict(msg.robot_status_report, preserving_proto_field_name=True)` and calling `store.set_robot_report(agent_id, d)`. Routes in `app.py` follow the existing `quik_orders.py`-style enqueue helpers (same auth + agent lookup as SetLimits push).

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/quik/ -q` — all PASS (existing + new).

- [ ] **Step 4: Commit**

```bash
git add trader/quik/ trader/api/app.py tests/quik/test_robot_hosting.py
git commit -m "feat(stl): agent robot deploy/control routes + status mirror in QuikAgentStore"
```

---

### Task 12: E2E smoke + migration runbook

**Files:**
- Create: `docs/runbooks/quik-robot-agent-rollout.md`

**Interfaces:** none new — this is verification + documented operator procedure.

- [ ] **Step 1: Write the runbook** with these exact sections (fill command output during execution):

```markdown
# Rollout: live-fvg-RIU6 -> QUIK agent

## Pre-flight (paper smoke on the QUIK VDS)
1. Operator: place robot-runner.exe next to quik-agent.exe (or publish via release).
2. Restart quik-agent. Verify console shows:
   - "runner-bridge listening on 127.0.0.1:50071"
   - "runner started pid=..."
   - "ready: quik=ok dde=ok runner=ok robots=0 trading_enabled=false"
3. From STL: POST /api/v1/quik/robots/live-fvg-RIU6/deploy-agent with paper=true.
4. Watch GET /api/v1/quik/agent/{id}/robots: heartbeat fresh, last_bar_unix advancing
   during the session, paper fills appearing on FVG signals.
5. Compare paper fills vs the STL backtest on the same bars (parity check).

## Cutover (operator-gated, real money)
1. STOP the STL-side robot: POST /api/v1/robots/live-fvg-RIU6/undeploy
   AND set state_json.live_real=false (DB) — never both paths live at once.
2. Re-deploy to agent with paper=false, max_position=1.
3. Operator arms the agent master flag (agent_config.json quik_trading_enabled=true)
   + QUIK terminal logged in. THIS IS THE HUMAN DECISION POINT.
4. Verify first real order lifecycle in the QUIK terminal + STL mirror.

## Rollback
- KillSwitch: POST /api/v1/quik/agent/{id}/robots/kill (blocks new + cancels working;
  position stays — close manually in QUIK if needed).
- Undeploy from agent; optionally re-arm the STL-side robot (reverse of step 1).

## Reboot drill (zero-touch)
- Reboot the QUIK VDS. Expect with NO manual steps: QUIK autostarts, agent service
  starts, runner supervised up, robots resumed from robots.json + runner_state.json,
  status mirror fresh in STL. The ONLY manual act ever: the master flag.
```

- [ ] **Step 2: Execute the paper smoke** (needs operator's QUIK VDS access — coordinate; do NOT arm anything).

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/quik-robot-agent-rollout.md
git commit -m "docs: QUIK robot-agent rollout runbook (paper smoke, cutover, reboot drill)"
```

---

## Self-review notes

- Spec coverage: engine (T7-9), QUIK execution (T4-5 via existing Manager/Guard/bridge), deploy handover + local truth (T1, T3, T5, T9), status reporting (T4-5, T9, T11), commands incl. kill scope (T5, T9), dual limits (T8 pre-send + existing Guard), zero-touch (T3 resume, T4 replay-on-connect, T6 supervisor+self-check, T7 reconnecting streams, T10 bundling), migration (T12). Auto-rollover, hot-reload, close-on-kill: explicitly out of scope per spec.
- The runner reuses `_parse_window/_within_window/_MSK` from `trader.lab.scheduler` — private names; acceptable inside the same repo (runner bundles the repo). If lint objects, re-export them publicly in scheduler.py rather than duplicating.
- `AgentRuntime` implements the `STLRuntime` protocol (what strategies consume). The spec's `AgentQuikBroker`/BrokerInterface naming is satisfied in substance; a full BrokerInterface adapter is NOT needed for v1 (YAGNI) — noted as a conscious deviation.
- protobuf gotcha called out at every regen step (grpcio-tools<1.71, header 5.29.0).
```
