# Agent Local Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local status page served by the QUIK agent (127.0.0.1:8071) showing link health with latency, robots with params/metrics, and a QUIK-fact reconciliation (positions/orders/trades/trans) with operator-confirmed align; the same JSON+page mirrored on STL.

**Architecture:** New Go packages `internal/accounts` (QUIK account tables snapshot), `internal/recon` (pure comparators + align plan), `internal/status` (HTTP server + embedded page + align executor). Lua publishes account tables + answers ping. Status JSON travels to STL as one new opaque-JSON frame; STL serves it verbatim and hosts the same static page.

**Tech Stack:** Go (agent), QLua (publisher), protobuf 5.29 (stubs), Python FastAPI (STL mirror), vanilla HTML/JS (page, go:embed).

## Global Constraints

- Python pb stubs: regenerate ONLY with `grpcio-tools<1.71`; generated header must read "Protobuf Python Version: 5.29.0" (prod runtime 5.29.6).
- Go builds/tests run ON THE HOSTER in `~/quik_build` (`export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH`); scp changed files up first.
- All new Lua publications and the STL snapshot frame are CHANGE-GATED (send only when payload changed).
- Status HTTP server binds `127.0.0.1` ONLY. The only mutating endpoints are `/api/align` and `/api/manual-offset`.
- Align order steps go through `trade.Manager` (Guard limits + master flag apply). A disarmed agent executes state-only steps.
- Synthetic/derived prices must be quantized to the instrument price step.
- No secret values in code, config, or logs (env var NAMES only).
- P&L rubles = points x `step_cost / price_step`; if coef unavailable, show points only, never a fabricated ruble number.
- Never restart `shectory-trader` while the operator live-tests trading.

---

### Task 1: Proto extensions + stub regeneration

**Files:**
- Modify: `proto/shectory/quik/v1/quik_agent.proto` (AgentMessage oneof, after field 15)
- Modify: `proto/shectory/quik/v1/runner_bridge.proto` (RunnerControl oneof, after field 6)
- Regenerate: `quik_agent/internal/pb/*`, `trader/quik/pb/*`, runner stubs (whatever `make gen` covers)

**Interfaces:**
- Produces: `AgentMessage.status_snapshot` (`AgentStatusSnapshot{status_json string=1, generated_at_unix_ms int64=2}`), `RunnerControl.fix_state` (`FixRobotState{robot_id string=1, set_position int64=2, set_avg_price double=3, clear_working bool=4, note string=5}`).

- [ ] **Step 1: Edit quik_agent.proto**

In `message AgentMessage` oneof payload add:

```proto
    AgentStatusSnapshot status_snapshot = 16; // local showcase JSON (opaque; STL serves verbatim)
```

After `message RobotStatusReport { ... }` add:

```proto
// Agent -> STL: the local showcase snapshot as opaque JSON (schema owned by the
// agent's internal/status package; STL stores + serves it verbatim, no parsing).
message AgentStatusSnapshot {
  string status_json = 1;
  int64 generated_at_unix_ms = 2;
}
```

- [ ] **Step 2: Edit runner_bridge.proto**

In `message RunnerControl` oneof payload add:

```proto
    FixRobotState fix_state = 7;  // recon align: force runner state to QUIK fact
```

At file end add:

```proto
// Recon align step: overwrite a robot's position/avg to the QUIK fact. clear_working
// drops the robot's belief in working orders (they were cancelled/never existed).
message FixRobotState {
  string robot_id = 1;
  int64 set_position = 2;
  double set_avg_price = 3;
  bool clear_working = 4;
  string note = 5;      // shown in the robot's journal
}
```

- [ ] **Step 3: Regenerate Go stubs on the hoster**

```bash
scp proto/shectory/quik/v1/*.proto hoster:~/quik_build/proto/shectory/quik/v1/
ssh hoster 'export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH && cd ~/quik_build && make gen'
scp 'hoster:~/quik_build/quik_agent/internal/pb/*' quik_agent/internal/pb/
```

- [ ] **Step 4: Regenerate Python stubs with the PINNED toolchain**

On the hoster, in the pinned venv (`grpcio-tools<1.71`, `protobuf==5.29.6`):

```bash
ssh hoster 'cd ~/apps/shectory-trader && <pinned-venv>/bin/python -m grpc_tools.protoc -I proto --python_out=trader/quik/pb --grpc_python_out=trader/quik/pb proto/shectory/quik/v1/quik_agent.proto proto/shectory/quik/v1/runner_bridge.proto'
```

Copy results back into the repo working tree.

- [ ] **Step 5: Verify the gencode header**

Run: `head -5 trader/quik/pb/shectory/quik/v1/quik_agent_pb2.py` (adjust to actual layout)
Expected: a line containing `Protobuf Python Version: 5.29.0`. If it says 6.x — STOP, wrong toolchain, do not commit.

- [ ] **Step 6: Verify Go compiles and Python imports**

```bash
ssh hoster 'export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$PATH && cd ~/quik_build/quik_agent && go build ./...'
poetry run python -c "from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as p; m=p.AgentMessage(); m.status_snapshot.status_json='{}'; print('ok')"
```

Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add proto/ quik_agent/internal/pb/ trader/quik/pb/
git commit -m "feat(proto): AgentStatusSnapshot frame + FixRobotState runner control"
```

---

### Task 2: Lua — ping/pong, server time, account tables

**Files:**
- Modify: `quik_agent/lua/shectory_trade.lua`
- Modify: `quik_agent/lua/shectory_trade_config.example.lua`

**Interfaces:**
- Produces (evt.jsonl, newline-JSON):
  - `{"event":"pong","t0":<echoed>,"ts":<lua now_ms>,"server_time":"HH:MM:SS"}`
  - `{"event":"acc_pos","rows":[[sec,net,avg] ...]}`
  - `{"event":"acc_ord","rows":[[order_num,sec,active(0|1),price,balance,qty] ...]}` (active + today's terminal)
  - `{"event":"acc_trd","rows":[[trade_num,order_num,sec,price,qty,ts_ms] ...]}` (incremental)
- Consumes (cmd.jsonl): `{"cmd":"ping","t0":<agent unix ms>}`

- [ ] **Step 1: Add the ping handler**

In `dispatch_command` (near `shectory_trade.lua:742`), alongside place/cancel/move:

```lua
  if cmd.cmd == "ping" then
    local st = ""
    local ok, v = pcall(getInfoParam, "SERVERTIME")
    if ok and v then st = v end
    emit({ event = "pong", t0 = cmd.t0 or 0, ts = now_ms(), server_time = st })
    return
  end
```

- [ ] **Step 2: Add change-gated account publishers**

Next to `publish_params()` (`shectory_trade.lua:549`), add three publishers using the existing `emit(tbl)`. Pattern (positions; orders/trades analogous):

```lua
local acc = { last_pos = "", last_ord = "", last_pos_ms = 0, last_ord_ms = 0, trd_seen = 0 }

local function publish_acc_positions()
  local n = getNumberOf("futures_client_holding") or 0
  local rows = {}
  for i = 0, n - 1 do
    local r = getItem("futures_client_holding", i)
    if r and r.sec_code and r.sec_code ~= "" then
      rows[#rows + 1] = { r.sec_code, tonumber(r.totalnet) or 0, tonumber(r.avrposnprice) or 0 }
    end
  end
  local key = ser_rows(rows)             -- deterministic string form, see Step 3
  if key ~= acc.last_pos then
    acc.last_pos = key
    emit({ event = "acc_pos", rows = rows })
  end
end

local function publish_acc_orders()
  local n = getNumberOf("orders") or 0
  local rows = {}
  for i = 0, n - 1 do
    local r = getItem("orders", i)
    if r and r.order_num then
      local active = 0
      if bit.band(tonumber(r.flags) or 0, 1) == 1 then active = 1 end
      rows[#rows + 1] = { tostring(r.order_num), r.sec_code or "", active,
                          tonumber(r.price) or 0, tonumber(r.balance) or 0, tonumber(r.qty) or 0 }
    end
  end
  local key = ser_rows(rows)
  if key ~= acc.last_ord then
    acc.last_ord = key
    emit({ event = "acc_ord", rows = rows })
  end
end

local function publish_acc_trades()  -- incremental: only rows we have not sent yet
  local n = getNumberOf("trades") or 0
  if n <= acc.trd_seen then return end
  local rows = {}
  for i = acc.trd_seen, n - 1 do
    local r = getItem("trades", i)
    if r and r.trade_num then
      local ts = now_ms()  -- QUIK datetime table conversion is best-effort; agent stamps receipt anyway
      rows[#rows + 1] = { tostring(r.trade_num), tostring(r.order_num or ""), r.sec_code or "",
                          tonumber(r.price) or 0, tonumber(r.qty) or 0, ts }
    end
  end
  acc.trd_seen = n
  if #rows > 0 then emit({ event = "acc_trd", rows = rows }) end
end
```

- [ ] **Step 3: Add `ser_rows` helper and pump wiring**

```lua
local function ser_rows(rows)
  local parts = {}
  for _, r in ipairs(rows) do parts[#parts + 1] = table.concat(r, ",") end
  return table.concat(parts, ";")
end
```

In `md_pump()` (`shectory_trade.lua:584`) add a 2s cadence block (mirror the existing interval pattern):

```lua
  if t - (md.last_acc_ms or 0) >= (CONFIG.ACC_INTERVAL_MS or 2000) then
    md.last_acc_ms = t
    pcall(publish_acc_positions)
    pcall(publish_acc_orders)
    pcall(publish_acc_trades)
  end
```

Note: `bit.band` — QUIK QLua ships `bit` library; if unavailable in the terminal's Lua, use `r.flags % 2 == 1` instead. Verify on the VDS smoke.

- [ ] **Step 4: Update the config example**

Add to `shectory_trade_config.example.lua`:

```lua
-- Account tables publish cadence for the agent showcase (ms). 0 keeps default 2000.
CONFIG.ACC_INTERVAL_MS = 2000
```

- [ ] **Step 5: Commit**

```bash
git add quik_agent/lua/
git commit -m "feat(lua): ping/pong + change-gated account tables (positions/orders/trades)"
```

(Runtime verification happens with Task 3's Go decoding tests + the operator VDS smoke at rollout.)

---

### Task 3: Go — decode new events, `internal/accounts` snapshot store

**Files:**
- Modify: `quik_agent/internal/trade/bridge.go` (luaEvent fields + dispatch)
- Create: `quik_agent/internal/accounts/accounts.go`
- Test: `quik_agent/internal/accounts/accounts_test.go`, extend `quik_agent/internal/trade/bridge_test.go` (or the existing bridge test file)

**Interfaces:**
- Produces: `accounts.Store` with:
  - `SetPositions(rows []Position)`, `SetOrders(rows []Order)`, `AddTrades(rows []Trade)`
  - `SetPong(t0Ms, luaTsMs int64, serverTime string)`
  - `SetTapeLag(exchTsMs, recvMs int64)` — fed from the tape MD sink (Task 9); keeps the freshest pair
  - `Snapshot() Snapshot` where `Snapshot{Positions []Position; Orders []Order; Trades []Trade; PosAgeMs, OrdAgeMs int64; RTTMs int64; ClockDriftMs int64; PongAgeMs int64; ExchangeLagMs int64}`
  - Types: `Position{Sec string; Net int64; Avg float64}`, `Order{Num, Sec string; Active bool; Price float64; Balance, Qty int64}`, `Trade{Num, OrderNum, Sec string; Price float64; Qty int64; TsMs int64}` (Trades ring-buffered, keep last 500)
- Produces: `Bridge.SetAccSink(func(AccEvent))` with `AccEvent{Kind string /* pos|ord|trd|pong */; Rows [][]any raw decoded; T0, TS int64; ServerTime string}` — keep decoding in the accounts adapter, mirroring how MDEvent works.

- [ ] **Step 1: Write failing decode test**

In the trade package test file:

```go
func TestDispatchAccountEvents(t *testing.T) {
	var got []AccEvent
	b := NewBridge(0, nil, nil)
	b.SetAccSink(func(e AccEvent) { got = append(got, e) })
	for _, line := range []string{
		`{"event":"acc_pos","rows":[["RIU6",2,89100.0]]}`,
		`{"event":"acc_ord","rows":[["123","RIU6",1,89000,1,1]]}`,
		`{"event":"acc_trd","rows":[["t1","123","RIU6",89050,1,1751700000000]]}`,
		`{"event":"pong","t0":100,"ts":200,"server_time":"12:00:01"}`,
	} {
		var ev luaEvent
		if err := json.Unmarshal([]byte(line), &ev); err != nil { t.Fatal(err) }
		b.dispatch(ev)
	}
	if len(got) != 4 || got[0].Kind != "pos" || got[3].ServerTime != "12:00:01" {
		t.Fatalf("got %+v", got)
	}
}
```

- [ ] **Step 2: Run it, expect FAIL** (`AccEvent` undefined): `ssh hoster '... && cd ~/quik_build/quik_agent && go test ./internal/trade/ -run TestDispatchAccountEvents'`

- [ ] **Step 3: Implement**

`luaEvent` gains: `Rows [][]any \`json:"rows"\``, `T0 int64 \`json:"t0"\``, `ServerTime string \`json:"server_time"\``. Add `AccEvent`, `SetAccSink` (same mutex pattern as SetMDSink), and dispatch cases `"acc_pos"/"acc_ord"/"acc_trd"/"pong"` mapping to Kind `pos/ord/trd/pong` before the handler switch.

- [ ] **Step 4: Write failing accounts.Store test** — cover: rows convert with malformed rows skipped; `AddTrades` accumulates + caps at 500; ages computed from an injected `now func() int64`; RTT = `now - t0` at pong receipt; drift = local MSK time-of-day minus `server_time` parsed as MSK time-of-day (handle midnight wrap by picking the smaller absolute value of `d` and `d ± 24h`).

```go
func TestStoreRTTAndDrift(t *testing.T) {
	now := int64(1000_000)
	s := New(func() int64 { return now })
	s.SetPong(now-150, 0, "03:00:00") // t0 150ms ago
	snap := s.Snapshot()
	if snap.RTTMs != 150 { t.Fatalf("rtt %d", snap.RTTMs) }
}
```

- [ ] **Step 5: Run to FAIL, implement `internal/accounts`, run to PASS**

Full package: mutex-guarded fields, converters `PositionFromRow/OrderFromRow/TradeFromRow([]any) (T, bool)` doing type-tolerant float64/string extraction (JSON numbers arrive as float64).

- [ ] **Step 6: Commit**

```bash
git add quik_agent/internal/trade/ quik_agent/internal/accounts/
git commit -m "feat(agent): decode account/pong events into accounts.Store"
```

---

### Task 4: Go — robots store timestamps

**Files:**
- Modify: `quik_agent/internal/robots/store.go`
- Test: `quik_agent/internal/robots/store_test.go`

**Interfaces:**
- Produces: `entry` gains `DeployedAtMs int64 \`json:"deployed_at_ms"\`` and `ParamsUpdatedAtMs int64 \`json:"params_updated_at_ms"\``; `Store.Put(spec)` sets DeployedAtMs once (preserved on re-Put); new `Store.TouchParams(robotID string) error` sets ParamsUpdatedAtMs=now; new `Store.Times(robotID) (deployedMs, paramsMs int64)`.

- [ ] **Step 1: Failing test** — Put sets deployed once and re-Put preserves it; TouchParams updates the second stamp; both survive a reload (`NewStore` on the same dir).
- [ ] **Step 2: Run to FAIL.**
- [ ] **Step 3: Implement** — add `times map[string][2]int64` loaded/saved through `entry`; inject `nowMs func() int64` on Store (default `time.Now().UnixMilli`) so tests are deterministic.
- [ ] **Step 4: Run to PASS** (`go test ./internal/robots/`).
- [ ] **Step 5: Wire TouchParams** — grep `SetRobotParams` in `quik_agent/internal/link/` and call `store.TouchParams(id)` where the spec's params_json is updated/relayed.
- [ ] **Step 6: Commit** — `git commit -m "feat(agent): persist robot deployed_at / params_updated_at"`

---

### Task 5: Go — retain last RobotStatus per robot

**Files:**
- Modify: `quik_agent/internal/runner/server.go`
- Test: `quik_agent/internal/runner/server_test.go`

**Interfaces:**
- Produces: `Server.LastStatuses() map[string]*quikv1.RobotStatus` (deep-copied via `proto.Clone`) and `Server.LastReportAgeMs() int64`.

- [ ] **Step 1: Failing test** — call `ReportStatus` twice with two robots, assert `LastStatuses()` has both, newest wins per robot_id, and mutation of the returned map does not affect internals.
- [ ] **Step 2: FAIL, implement** — in `ReportStatus` (server.go:97) store `r.Robots` into `s.lastStatus map[string]*quikv1.RobotStatus` under `s.mu`.
- [ ] **Step 3: PASS, commit** — `git commit -m "feat(agent): runner server retains last per-robot status"`

---

### Task 6: Go — `internal/recon`: comparators + align plan

**Files:**
- Create: `quik_agent/internal/recon/recon.go`, `quik_agent/internal/recon/plan.go`
- Test: `quik_agent/internal/recon/recon_test.go`
- Modify: `quik_agent/internal/trade/manager.go` (working-orders accessor)

**Interfaces:**
- Consumes: `accounts.Snapshot` (Task 3), `runner.Server.LastStatuses()` (Task 5).
- Produces:

```go
package recon

type RobotView struct {
	ID, Symbol string
	Paper      bool
	Position   int64
	AvgPrice   float64
	OrderNums  []string          // QUIK order_nums of the robot's working orders
	FillKeys   []FillKey         // recent real fills (paper robots contribute none)
}
type FillKey struct{ OrderNum string; Qty int64; Price float64 }

type Inputs struct {
	Robots       []RobotView
	HumanOrders  map[string]bool   // order_nums owned by the human path (Manager, non-"rr:")
	Acc          AccView           // adapted from accounts.Snapshot
	Trans        []TransCheck      // pre-flagged hung/rejected trans from the Manager's pending state
	ManualOffset map[string]int64  // symbol -> operator-declared manual position
	NowMs        int64
}
type AccView struct {
	Positions []Position; Orders []Order; Trades []Trade
	PosAgeMs, OrdAgeMs int64
}

type Report struct {
	State     string       // "OK" | "MISMATCH" | "STALE"
	Positions []PosCheck   // {Symbol, RobotsSum, Quik, ManualOffset int64; OK bool}
	Orders    []OrderCheck // {OrderNum, Owner string; OK bool}  Owner: robot id | "human" | "ORPHAN" | "MISSING:<robot>"
	Trades    []TradeCheck // {TradeID, OrderNum string; Matched bool}
	Trans     []TransCheck // {TransID int64; Status, Text string; OK bool} — hung/rejected only
	Plan      *Plan        // nil when State != "MISMATCH"
}
type Plan struct{ ID string; Steps []Step }
type Step struct {
	Kind     string // "cancel_order" | "close_position" | "fix_state"
	Detail   string // human-readable, page shows verbatim
	Symbol   string
	OrderNum string
	Qty      int64  // close_position: signed delta to trade (negative = sell)
	RobotID  string // fix_state target
	SetPos   int64
	SetAvg   float64
}

func Evaluate(in Inputs) Report
```

- Plan.ID = first 12 hex chars of sha256 over the canonical JSON of Steps + `PosAgeMs/OrdAgeMs`. Deterministic: same inputs => same ID.
- Rules: STALE when `PosAgeMs > 30_000 || OrdAgeMs > 30_000` (report STALE, no plan, never false OK). Paper robots are EXCLUDED from all QUIK matching (their rows never touch QUIK). Positions: per symbol, `sum(real robots) + ManualOffset[symbol] == Quik.Net`. Orders: every real-robot OrderNum must be active in QUIK (else MISSING => fix_state step); every active QUIK order must be a robot's or human's (else ORPHAN => cancel_order step). Trades: every robot FillKey matches a QUIK trade by OrderNum with qty/price within one price step; unmatched flagged (informational, no auto step). Position excess => one close_position step per symbol with `Qty = Quik.Net - robotsSum - ManualOffset` (the plan trades AGAINST the robots' books only via fix_state by default; close_position is generated only when no robot claims the excess — detail string must say which).

- [ ] **Step 1: Write the failing tests first** — table-driven, minimum cases: all-green OK; stale tables => STALE; orphan QUIK order => cancel step; robot MISSING order => fix_state with clear detail; position off by manual offset => OK; position off without offset => plan with signed Qty; paper robot never contributes; plan ID stable across two Evaluate calls and changes when steps change.

```go
func TestEvaluateOrphanOrder(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Symbol: "RIU6"}},
		Acc: AccView{Orders: []Order{{Num: "555", Sec: "RIU6", Active: true}},
			PosAgeMs: 1000, OrdAgeMs: 1000},
		NowMs: 1,
	}
	rep := Evaluate(in)
	if rep.State != "MISMATCH" || rep.Plan == nil { t.Fatalf("%+v", rep) }
	if rep.Plan.Steps[0].Kind != "cancel_order" || rep.Plan.Steps[0].OrderNum != "555" {
		t.Fatalf("%+v", rep.Plan.Steps)
	}
}
```

- [ ] **Step 2: Run to FAIL, implement recon.go + plan.go, run to PASS** (`go test ./internal/recon/ -v`).
- [ ] **Step 3: Manager accessor** — add to manager.go:

```go
// WorkingSnapshot is a read-only view of one live working order for the showcase.
type WorkingSnapshot struct {
	ClientID, OrderNum, Code string
	Price                    float64
	Qty, Balance             int64
}

func (m *Manager) SnapshotWorking() []WorkingSnapshot
```

(iterate `m.working` under the manager's lock, skip `done`). Also add `func (m *Manager) PendingTrans() []recon.TransCheck` equivalent (plain struct in trade, adapted in status): orders still PENDING past the reconcile window or last-rejected trans replies, sourced from the same state `reconcileStalePending` uses. Test: place via the existing fake bridge path used by manager tests, assert snapshot contents.

Wiring note for Task 9: the tape MD sink in main.go additionally calls `accStore.SetTapeLag(lastTradeTsMs, time.Now().UnixMilli())` from the freshest tape row, and `Snapshot().ExchangeLagMs` is annotated unreliable on the page when `|ClockDriftMs| > 1500`.
- [ ] **Step 4: PASS, commit** — `git commit -m "feat(agent): recon comparators + deterministic align plan"`

---

### Task 7: Go — `internal/status`: JSON builder, HTTP server, embedded page

**Files:**
- Create: `quik_agent/internal/status/status.go` (builder + types), `quik_agent/internal/status/server.go` (HTTP), `quik_agent/internal/status/page.html` (go:embed), `quik_agent/internal/status/strategies.go` (strategies_doc.json loader)
- Test: `quik_agent/internal/status/status_test.go`, `quik_agent/internal/status/server_test.go`

**Interfaces:**
- Consumes: `accounts.Store.Snapshot()`, `runner.Server.LastStatuses()/RunnerHealthy()`, `robots.Store.All()/Paused()/Times()`, `trade.Manager.SnapshotWorking()`, `quikdde.Provider` (tick freshness + params for coef), `health` snapshot fields, link state via injected funcs.
- Produces:

```go
type Deps struct {
	Accounts   *accounts.Store
	Robots     *robots.Store
	Runner     *runner.Server
	Manager    *trade.Manager
	Provider   *quikdde.Provider
	LinkUp     func() bool
	Reconnects func() uint32
	UptimeSec  func() int64
	MasterFlag bool
	BuildRev   uint32
	Version    string
	ManualGet  func() map[string]int64          // from config
	ManualSet  func(map[string]int64) error     // persists to agent_config.json
	AlignExec  func(plan recon.Plan) []StepResult // Task 8
	LogPaths   map[string]string                // "agent"/"runner" -> file path
	DocsPath   string                           // strategies_doc.json (Task 10)
	NowMs      func() int64
}
func BuildStatus(d Deps) ([]byte, error)   // the /api/status JSON (spec schema)
func NewServer(d Deps) *http.Server        // all routes; Addr set by caller
```

- JSON schema: exactly the spec's (agent/health/robots/recon top-level keys). `pnl_rub` present only when coef is known.

- [ ] **Step 1: Failing builder test** — fake deps (small interfaces or funcs), assert: JSON has all top-level keys; a paper robot renders `"mode":"paper"`; missing coef => no `pnl_rub` key; recon block comes from `recon.Evaluate` and STALE gating works.
- [ ] **Step 2: FAIL, implement `BuildStatus`, PASS.**
- [ ] **Step 3: Failing server test (httptest)** — `GET /api/status` returns 200 + `application/json`; `GET /logs/agent` streams tail (write a temp file, expect last bytes); `GET /strategy/fvg` returns doc from a temp strategies_doc.json; `POST /api/align` with a stale plan_id returns 409 + fresh plan; `POST /api/manual-offset {"RIU6":2}` calls ManualSet.
- [ ] **Step 4: FAIL, implement server.go** — `http.NewServeMux`; align handler: recompute recon via BuildStatus's internals, compare plan_id, 409 on mismatch, else run `AlignExec` and return step results. Tail helper: open file, `Seek(-65536, io.SeekEnd)` clamped, copy.
- [ ] **Step 5: page.html** — single file, go:embed, no external assets. Structure:

```html
<!-- header: agent version/build/uptime + master flag plaque -->
<!-- section 1: health tiles: feed per-instrument age | QUIK RTT | exchange lag + clock drift | STL link ("не влияет на торговлю") -->
<!-- section 2: robots table (mode plaque REAL red / paper grey; expandable params row; links) -->
<!-- section 3: recon: state banner; positions/orders/trades/trans tables; align plan card with button -->
<!-- footer: "agent unreachable" grey overlay when fetch fails -->
<script>
const qs = new URLSearchParams(location.search);
const SRC = qs.get('src') || '/api/status';
const INTERVAL = +(qs.get('interval') || 1000);
let lastOkMs = 0;
async function poll() {
  try {
    const r = await fetch(SRC, {cache: 'no-store'});
    render(await r.json()); lastOkMs = Date.now();
    document.body.classList.remove('offline');
  } catch (e) { if (Date.now() - lastOkMs > 5000) document.body.classList.add('offline'); }
  setTimeout(poll, INTERVAL);
}
async function align(planId) {
  if (!confirm('Выровнять по плану ' + planId + '?')) return;
  const r = await fetch('/api/align', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({plan_id: planId})});
  alert(await r.text());
}
poll();
</script>
```

`render(s)` fills the DOM from the status JSON; freshness > 5s dims numbers (CSS class); MISMATCH plays a short `AudioContext` beep once per transition. Dark theme, borrow the AgentRobotScreen look (badges, mono numbers). No framework, no external fonts.
- [ ] **Step 6: PASS all** (`go test ./internal/status/ -v`), **commit** — `git commit -m "feat(agent): local status server + embedded showcase page"`

---

### Task 8: Align execution + runner FixRobotState + alerts

**Files:**
- Modify: `quik_agent/internal/status/align.go` (create), `quik_agent/internal/runner/server.go` (relay fix_state), `robot_runner/host.py` (handle fix_state), `robot_runner/bridge_client.py` (decode if needed)
- Test: `quik_agent/internal/status/align_test.go`, `tests/runner/test_fix_state.py`

**Interfaces:**
- Consumes: `recon.Plan` (Task 6), `RunnerControl.fix_state` (Task 1).
- Produces: `type StepResult struct{ Step recon.Step; OK bool; Error string }`; `Aligner{Manager *trade.Manager; Runner *runner.Server; Provider *quikdde.Provider; NowMs func() int64}.Execute(plan recon.Plan) []StepResult`; `runner.Server.SendFixState(fix *quikv1.FixRobotState) error` (pushes into the StreamControl channel like deploy/undeploy relays — mirror the existing relay method there).

- [ ] **Step 1: Failing Go test** — fake manager/runner: `cancel_order` step calls CancelOrder with the order_num; `close_position` places a limit at the provider's last price quantized to price step (test asserts quantization: last=89173, step=10 => price 89170) with `client_id` = `"recon:"+plan.ID`; `fix_state` calls SendFixState; a step error stops the sequence and reports it.
- [ ] **Step 2: FAIL, implement align.go, PASS.**
- [ ] **Step 3: Failing Python test** — in `tests/runner/test_fix_state.py`: build the host with a deployed paper robot (reuse the existing host-test fixtures in `tests/runner/`), feed a `RunnerControl(fix_state=FixRobotState(robot_id=..., set_position=2, set_avg_price=89000, clear_working=True, note="recon"))`, assert the robot's position/avg/working are overwritten, the note lands in the journal, and `runner_state.json` persists the fix.
- [ ] **Step 4: FAIL, implement in host.py control loop (next to deploy/pause handling), PASS** (`poetry run pytest tests/runner/ -q`).
- [ ] **Step 5: Alerts** — in the recon evaluation loop (wired in Task 9), on OK->MISMATCH after 10s debounce call the link's `EmitAlert(sev, "RECON_MISMATCH", detail)` — severity CRITICAL if any involved robot has `paper=false`, else WARN; on MISMATCH->OK emit `RECON_RECOVERED` INFO. Unit-test the debounce as a small pure `reconAlerter` struct in internal/status with injected clock.
- [ ] **Step 6: Commit** — `git commit -m "feat: operator-confirmed align (cancel/close/fix_state) + recon alerts"`

---

### Task 9: Wiring — config, main.go, ping loop, snapshot frame to STL

**Files:**
- Modify: `quik_agent/internal/config/config.go`, `quik_agent/cmd/quik-agent/main.go`, `quik_agent/internal/link/link.go` + `quik_agent/internal/link/trade.go` (snapshot send), `quik_agent/internal/trade/bridge.go` (ping cmd append)
- Test: extend `quik_agent/internal/link` tests for the change-gate

**Interfaces:**
- Config keys (all additive, defaults in `applyDefaults`): `StatusPort int \`json:"status_port"\`` default 8071 (0 disables), `ReconManualOffset map[string]int64 \`json:"recon_manual_offset"\`` default `{}`, `StatusSnapshotMinSec int \`json:"status_snapshot_min_sec"\`` default 5.
- Produces: `Bridge.SendPing(t0 int64) error` (appends `{"cmd":"ping","t0":...}`); `Link.EmitStatusSnapshot(json []byte, genMs int64) error` (drops quietly when stream nil, same as other emits).

- [ ] **Step 1: Config fields + defaults + test** (existing config test pattern; assert defaults on empty JSON).
- [ ] **Step 2: main.go wiring** — after the robot-hosting block: build `accounts.New(...)`, `bridge.SetAccSink(adapter)`, ping ticker every 5s (`bridge.SendPing(time.Now().UnixMilli())`), construct `status.Deps{...}` and `status.NewServer`, serve on `127.0.0.1:<StatusPort>` in a goroutine (skip when 0), and start the recon/alert loop (every 5s: BuildStatus by the server is on-demand; the alert loop calls `recon.Evaluate` directly with the same inputs).
- [ ] **Step 3: Snapshot to STL** — in the link's heartbeat/diagnostics loop: every tick, build the status JSON (reuse `status.BuildStatus`), send `AgentStatusSnapshot` ONLY if `sha256(json)` changed AND `now-lastSent >= StatusSnapshotMinSec` (change-gate test with a fake stream, mirroring existing link emit tests).
- [ ] **Step 4: Full agent test suite on the hoster** — scp the changed tree, `go build ./... && go test ./...`. Expected: all green.
- [ ] **Step 5: Commit** — `git commit -m "feat(agent): wire status server, ping loop, change-gated status snapshot to STL"`

---

### Task 10: strategies_doc.json in the runner build

**Files:**
- Modify: `deploy/build_runner.sh`
- Create: `robot_runner/export_docs.py`
- Test: `tests/runner/test_export_docs.py`

**Interfaces:**
- Produces: `dist/runner/strategies_doc.json`: `{"<strategy_id>": {"title": str, "doc": str, "params": {name: default}}}` exported from `trader/lab/strategies/library.py`'s registry. Agent reads it from next to the runner exe (`Deps.DocsPath`, Task 7).

- [ ] **Step 1: Failing test** — `export_docs.build_docs()` returns a dict containing key `"fvg"` with non-empty `doc` and a `params` dict.
- [ ] **Step 2: FAIL, implement export_docs.py** (import the registry, read docstrings + default params the same way the backtester enumerates them), **PASS**.
- [ ] **Step 3: build_runner.sh** — after the PyInstaller step add: `poetry run python -m robot_runner.export_docs "$DIST_DIR/strategies_doc.json"` and include the file in what gets staged for the release zip.
- [ ] **Step 4: Commit** — `git commit -m "feat(runner): export strategies_doc.json into the release for the local showcase"`

---

### Task 11: STL mirror — frame handling, store, endpoint

**Files:**
- Modify: `trader/quik/server.py` (payload switch, near line 267), `trader/quik/store.py` (retention), `trader/api/quik_robots.py` (endpoint)
- Test: `tests/quik/test_agent_status_mirror.py`

**Interfaces:**
- Produces: `QuikAgentStore.set_agent_status(agent_id, status_json: str, generated_at_ms: int)` + `agent_status(agent_id) -> dict | None` (parsed JSON + `received_at_ms`); `GET /api/v1/quik/agent-local-status?agent_id=` (agent_id optional, `_pick` semantics like robots-mirror) returning the stored JSON verbatim plus `{"_received_at_ms": ...}`.

- [ ] **Step 1: Failing pytest** — feed a fake AgentMessage with `status_snapshot` through the server handler path the way existing store tests do (see `tests/quik/test_store.py` fixtures), then call the endpoint via the app test client; assert verbatim JSON + received_at.
- [ ] **Step 2: FAIL, implement all three files, PASS** (`poetry run pytest tests/quik/ -q`).
- [ ] **Step 3: Lint + full unit suite** — `poetry run ruff check trader/ tests/ robot_runner/ && poetry run pytest -m "not integration" -q`.
- [ ] **Step 4: Commit** — `git commit -m "feat(stl): mirror agent status snapshot + agent-local-status endpoint"`

---

### Task 12: STL frontend page + deploy + runbook

**Files:**
- Create: `frontend/public/agent-status.html` (copy of `quik_agent/internal/status/page.html`)
- Modify: `docs/runbooks/quik-robot-agent-rollout.md` (showcase section), `CLAUDE.md` (one line: status page + recon)

**Interfaces:**
- Consumes: the page's `?src=&interval=` params (Task 7). On STL it is opened as `https://stl.shectory.ru/agent-status.html?src=/api/v1/quik/agent-local-status&interval=10000`.

- [ ] **Step 1: Copy the page** — literal copy; add a build note comment at the top: `<!-- SOURCE OF TRUTH: quik_agent/internal/status/page.html — edit there, copy here -->`.
- [ ] **Step 2: Frontend build** — `cd frontend && node ./node_modules/vite/bin/vite.js build`; verify `dist/agent-status.html` exists.
- [ ] **Step 3: Runbook** — add: VDS operator steps (update `shectory_trade.lua` + config sidecar, restart script, check `http://127.0.0.1:8071`), align procedure (what the button does, master-flag note), STL mirror URL, and the clock-drift caveat.
- [ ] **Step 4: Deploy (SAFE procedure)** — backend: `git push`, `ssh hoster 'cd ~/apps/shectory-trader && git pull'`, restart `shectory-trader` (NOT during live trading tests); frontend: scp `dist/index.html` + hashed assets + `dist/agent-status.html`; agent: `bash ~/quik_build/publish_quik_agent.sh` with the Windows-built runner exe staged (numeric epoch build_rev).
- [ ] **Step 5: Verify live** — `curl -s https://stl.shectory.ru/api/v1/quik/agent-local-status | head -c 400` shows the snapshot after the agent self-updates; operator confirms the local page renders and recon is green (or STALE until the Lua update is applied on the VDS).
- [ ] **Step 6: Commit** — `git commit -m "feat(showcase): STL mirror page + rollout runbook for the agent local showcase"`

---

## Execution notes

- Task order is dependency order; Tasks 3-8 are agent-local and testable without the hoster except for running `go test` there.
- `RobotFill.order_id`: Task 6's trade matching assumes real fills carry the QUIK order_num in `order_id`. VERIFY in `robot_runner/runtime.py` when implementing Task 6; if paper fills use synthetic ids, they are excluded anyway (paper robots contribute no FillKeys).
- The VDS Lua/script update is operator-manual; until applied, the recon block correctly shows STALE and everything else works.
