# Robot Tagging + GUI Robot Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile ONLY agent robots by a robot-ID tag in each order/trade comment (manual trading shown but never reconciled/aligned), and manage robots from the showcase GUI — edit params anywhere (local + authed STL), toggle paper/real ONLY from the local console behind a flat-precondition + typed-confirm gate.

**Architecture:** Feature A tags QUIK orders via the transaction COMMENT (surfacing as `brokerref`), carries it through the Lua acc publishers → accounts.Store → recon, which classifies by tag (robot / "recon" agent / MANUAL) and reconciles robots by orders + recent-trade match, showing manual separately. Feature B adds two agent-local HTTP endpoints (params, mode) plus an STL param relay; the mode endpoint exists ONLY on the agent (topological local-only) and refuses a non-flat robot.

**Tech Stack:** Go (agent), QLua (publisher), Python FastAPI (STL relay), vanilla HTML/JS (page). NO proto changes (tag rides the Lua row; mode reuses DeployRobot; params reuse SetRobotParams).

## Global Constraints

- QUIK QLua is 32-bit: encode large ints as `string.format("%.0f", v)`, never `%d`; empty tables as `[]` (`if is_array then`). (Already fixed; do not regress.)
- Go builds/tests run ON THE HOSTER in `~/quik_build` (`export PATH=$HOME/go-sdk/go/bin:$HOME/go/bin:$HOME/protoc/bin:$PATH`); scp changed files up first. No local Go toolchain.
- Python pb stubs are NOT regenerated (no proto change this plan).
- All new Lua publications stay CHANGE-GATED / keepalive as already implemented.
- The paper/real mode endpoint MUST exist ONLY on the agent's local status server — never added to STL in any form.
- The mode flip is refused unless the robot is FLAT (position 0, no working orders, no unresolved trans) in BOTH directions AND the typed `confirm_id` equals the robot ID.
- An align plan step must NEVER target an untagged (MANUAL) order or the account net.
- `manual_offset` is REMOVED (config key, Deps.ManualGet/ManualSet, the page editor, `/api/manual-offset`).
- No secret values in code/logs. Commit on main, plain git.

---

### Task 1: Lua — stamp robot tag into COMMENT; publish brokerref

**Files:**
- Modify: `quik_agent/lua/shectory_trade.lua` (`handle_place` ~748; `publish_acc_orders` ~594; `publish_acc_trades` ~632)

**Interfaces:**
- Consumes: place command gains a `comment` field (Task 2 sends it).
- Produces (evt.jsonl rows, backward-additive trailing field):
  - `acc_ord`: `[order_num, sec, active(0|1), price, balance, qty, brokerref]`
  - `acc_trd`: `[trade_num, order_num, sec, price, qty, ts_ms, brokerref]`

- [ ] **Step 1: Stamp COMMENT in `handle_place`.** In the `trans = {...}` table (after `ACCOUNT`), add:

```lua
  }
  if client_code ~= "" then trans.CLIENT_CODE = tostring(client_code) end
  -- Robot attribution: the agent sends the owner tag (robot ID / "recon"); QUIK stores
  -- a NEW_ORDER COMMENT in the order's brokerref, inherited by its trades, so recon can
  -- tell a robot order from manual trading. Empty for manual/unknown (never happens from
  -- the agent path). Length is bounded by the QUIK build — verified in the go-live smoke.
  if cmd.comment and cmd.comment ~= "" then trans.COMMENT = tostring(cmd.comment) end
```

- [ ] **Step 2: Add brokerref to `publish_acc_orders` row.** Change the row build:

```lua
      rows[#rows + 1] = { tostring(r.order_num), r.sec_code or "", active,
                          tonumber(r.price) or 0, tonumber(r.balance) or 0, tonumber(r.qty) or 0,
                          tostring(r.brokerref or "") }
```

- [ ] **Step 3: Add brokerref to `publish_acc_trades` row.** Change the row build:

```lua
      rows[#rows + 1] = { tostring(r.trade_num), tostring(r.order_num or ""), r.sec_code or "",
                          tonumber(r.price) or 0, tonumber(r.qty) or 0, ts,
                          tostring(r.brokerref or "") }
```

- [ ] **Step 4: Verify structurally** (no Lua harness). Re-read the three edited blocks for `if/then/end` balance; run `grep -n "COMMENT\|brokerref" quik_agent/lua/shectory_trade.lua` and confirm the three additions. ASCII-only edits; do not re-encode CP1251 bytes.

- [ ] **Step 5: Commit**

```bash
git add quik_agent/lua/shectory_trade.lua
git commit -m "feat(lua): stamp robot tag into order COMMENT; publish brokerref on acc_ord/acc_trd"
```

(Runtime verification is Task 2's Go decode + the go-live smoke.)

---

### Task 2: Go — placeCmd.Comment (owner tag) + accounts Order/Trade.Tag decode

**Files:**
- Modify: `quik_agent/internal/trade/bridge.go` (`placeCmd` ~52)
- Modify: `quik_agent/internal/trade/manager.go` (place cmd build ~266)
- Modify: `quik_agent/internal/accounts/accounts.go` (`Order` ~25, `Trade` ~34, `OrderFromRow` ~368, `TradeFromRow` ~400)
- Test: `quik_agent/internal/trade/bridge_test.go`, `quik_agent/internal/accounts/accounts_test.go`

**Interfaces:**
- Produces: `placeCmd.Comment string \`json:"comment"\``; an EXPORTED `trade.RobotIDFromClientID(clientID string) (robotID string, ok bool)` (parses `rr:<robotID>:<seq>`) reused by status + main; a helper `ownerTag(clientID string) string` in trade (robot ID for `rr:<id>:...`, `"recon"` for `recon:...`, else the raw clientID). `accounts.Order.Tag string`, `accounts.Trade.Tag string`; `OrderFromRow`/`TradeFromRow` read the 7th element as the tag (absent/short row => "").

- [ ] **Step 1: Failing test — ownerTag + place cmd carries it.** In `bridge_test.go` (trade package):

```go
func TestOwnerTag(t *testing.T) {
	cases := map[string]string{
		"rr:agent-fvg-RIU6-v2:1": "agent-fvg-RIU6-v2",
		"recon:ab8fffa61d4a:0":   "recon",
		"human-7":                "human-7",
		"":                       "",
	}
	for in, want := range cases {
		if got := ownerTag(in); got != want {
			t.Fatalf("ownerTag(%q)=%q want %q", in, got, want)
		}
	}
}
```

- [ ] **Step 2: Run FAIL** (`ownerTag` undefined): `ssh hoster '... && cd ~/quik_build/quik_agent && go test ./internal/trade/ -run TestOwnerTag'`

- [ ] **Step 3: Implement.** In `bridge.go` add to `placeCmd` after `Account`: `Comment string \`json:"comment"\``. Add `ownerTag`:

```go
// RobotIDFromClientID parses "rr:<robotID>:<seq>" -> (robotID, true); anything else is
// (‑, false). Exported because status (recon inputs) and main (mode flat-gate) both need it.
func RobotIDFromClientID(clientID string) (string, bool) {
	const p = "rr:"
	if !strings.HasPrefix(clientID, p) {
		return "", false
	}
	rest := clientID[len(p):]
	i := strings.LastIndex(rest, ":")
	if i <= 0 {
		return "", false
	}
	return rest[:i], true
}

// ownerTag maps a client_id to the tag written into the QUIK order COMMENT so recon can
// attribute the order: a robot's ID for "rr:<robotID>:<seq>", "recon" for an align order
// ("recon:<planID>:<n>"), else the raw client_id. Empty stays empty (manual/unknown).
func ownerTag(clientID string) string {
	if rid, ok := RobotIDFromClientID(clientID); ok {
		return rid
	}
	if strings.HasPrefix(clientID, "recon:") {
		return "recon"
	}
	return clientID
}
```

Status already has an unexported `robotIDFromClientID`; switch its call sites to
`trade.RobotIDFromClientID` (or leave the private one — they must agree; prefer the shared
exported one). In `manager.go` place build (~266) add `Comment: ownerTag(req.GetClientId()),`.

- [ ] **Step 4: Run PASS.**

- [ ] **Step 5: Failing accounts decode test.** In `accounts_test.go`:

```go
func TestOrderTradeFromRowTag(t *testing.T) {
	o, ok := OrderFromRow([]any{"555", "RIU6", 1.0, 89000.0, 1.0, 1.0, "agent-fvg-RIU6-v2"})
	if !ok || o.Tag != "agent-fvg-RIU6-v2" {
		t.Fatalf("order tag: %+v ok=%v", o, ok)
	}
	// Old Lua (no 7th element) => empty tag, still decodes.
	o2, ok2 := OrderFromRow([]any{"555", "RIU6", 1.0, 89000.0, 1.0, 1.0})
	if !ok2 || o2.Tag != "" {
		t.Fatalf("legacy order: %+v ok=%v", o2, ok2)
	}
	tr, ok3 := TradeFromRow([]any{"t1", "555", "RIU6", 89050.0, 1.0, 1.75e12, "recon"})
	if !ok3 || tr.Tag != "recon" {
		t.Fatalf("trade tag: %+v ok=%v", tr, ok3)
	}
}
```

- [ ] **Step 6: Run FAIL, implement.** Add `Tag string` to `Order` and `Trade`. In `OrderFromRow`/`TradeFromRow`, after the existing fields, read the trailing tag if present: `if len(row) >= 7 { if s, ok := row[6].(string); ok { out.Tag = s } }` (Order) / `if len(row) >= 7` for Trade. Keep the existing required-length check unchanged (tag is optional). Surface Tag through `Snapshot()` (the Positions/Orders/Trades copies already deep-copy structs — Tag rides along).

- [ ] **Step 7: Run PASS, full suite** (`go test ./internal/trade/ ./internal/accounts/`), **commit**

```bash
git add quik_agent/internal/trade/ quik_agent/internal/accounts/
git commit -m "feat(agent): owner tag on place cmd + brokerref tag decode into Order/Trade"
```

---

### Task 3: recon — tag classification, manual block, per-robot self-consistency, drop manual_offset

**Files:**
- Modify: `quik_agent/internal/recon/recon.go` (types + Evaluate), `quik_agent/internal/recon/plan.go` (no ID change; ensure no manual step)
- Test: `quik_agent/internal/recon/recon_test.go` (substantial rewrite)

**Interfaces:**
- Consumes: `AccView.Orders[i].Tag`, `AccView.Trades[i].Tag` (Task 2 types mirrored here).
- Produces:
  - `RobotView` gains `Tag string` (== the robot ID used in the COMMENT).
  - `recon.Order`/`recon.Trade` gain `Tag string`.
  - `Inputs.ManualOffset` REMOVED. `Inputs` gains nothing else (robot tags come from RobotView).
  - `Report` gains `Manual ManualView` where `type ManualView struct { Orders []ManualOrder; AccountNet []PosLine }`, `type ManualOrder struct { OrderNum, Sec string }`, `type PosLine struct { Sec string; Net int64 }`.
  - `OrderCheck` rows are produced ONLY for robot-attributable orders: `Owner` = robot ID (OK=true when the robot knows the order_num; OK=false = ROBOT_ORPHAN) or `"MISSING:<robotID>"` (robot's order absent from QUIK). MANUAL and `"recon"` orders produce NO OrderCheck — manual goes to `Report.Manual.Orders`, recon is skipped entirely.
  - Position: NO PosCheck against account-net. Each robot gets a self-consistency verdict from orders + recent trades. Report keeps a per-robot summary (add `RobotChecks []RobotCheck` with `{ID, Symbol string; Position int64; OrdersOK, TradesOK bool}`).

- [ ] **Step 1: Write the failing tests first** (table-driven; the minimum set):
  - a QUIK order tagged with a deployed robot's tag, and the robot knows its order_num → OK, owner = robot ID.
  - a QUIK order tagged with a robot, robot does NOT know it → OK=false, owner = robot ID (ROBOT_ORPHAN), and a `cancel_order` step IS generated.
  - a QUIK order with empty tag (manual) → owner `"MANUAL"`, OK=true (never a mismatch), appears in `Report.Manual.Orders`, and generates NO step. (Invariant #1.)
  - a QUIK order tagged `"recon"` → agent align, not manual, not a mismatch, no step.
  - a robot's believed working order absent from QUIK → `MISSING:<robotID>` + fix_state step.
  - a robot fill (FillKey) with a matching tagged QUIK trade → TradesOK; with NO matching tagged trade → TradesOK=false (mismatch); a tagged QUIK trade the robot never recorded → mismatch.
  - `Report.State` is OK when only manual activity exists (no robot findings). (Invariant #5.)
  - a purely-manual account (net +15, 6 untagged orders) → State OK, Manual block populated, zero steps.
  - determinism: two Evaluate calls with shuffled slices produce identical Report + plan ID.

```go
func TestEvaluateManualNeverAligned(t *testing.T) {
	in := Inputs{
		Robots: []RobotView{{ID: "r1", Tag: "r1", Symbol: "RIU6"}},
		Acc: AccView{
			Positions: []Position{{Sec: "RIU6", Net: 15}},
			Orders: []Order{
				{Num: "555", Sec: "RIU6", Active: true, Tag: ""},        // manual
				{Num: "777", Sec: "RIU6", Active: true, Tag: "recon"},   // agent align
			},
			PosAgeMs: 100, OrdAgeMs: 100, PosAtMs: 1, OrdAtMs: 1,
		},
		NowMs: 1,
	}
	rep := Evaluate(in)
	if rep.State != "OK" {
		t.Fatalf("manual-only + agent align must be OK, got %s", rep.State)
	}
	if rep.Plan != nil && len(rep.Plan.Steps) != 0 {
		t.Fatalf("no align step may target manual/recon: %+v", rep.Plan.Steps)
	}
	if len(rep.Manual.Orders) != 1 || rep.Manual.Orders[0].OrderNum != "555" {
		t.Fatalf("manual order 555 must be in the Manual block: %+v", rep.Manual)
	}
	if len(rep.Manual.AccountNet) != 1 || rep.Manual.AccountNet[0].Net != 15 {
		t.Fatalf("account net 15 must be shown for context: %+v", rep.Manual.AccountNet)
	}
}
```

- [ ] **Step 2: Run to FAIL, implement recon.go, run to PASS.** Rewrite `Evaluate`:
  - Build `robotByTag := map[string]RobotView` from Robots (skip paper for QUIK matching, same as today).
  - For each `Acc.Order`: `tag=o.Tag`. If `tag==""` → MANUAL (append to `Report.Manual.Orders`, no check row that fails). If `tag=="recon"` → agent, skip. If `robotByTag[tag]` exists → check the robot knows `o.Num` (in its OrderNums): yes → OK; no → OrderCheck OK=false owner=tag + `cancel_order` step (ROBOT_ORPHAN). If tag matches no deployed robot → treat as MANUAL (unknown tag is not ours to touch).
  - For each robot's believed OrderNum not active in `Acc.Orders` → `MISSING:<id>` + fix_state step.
  - Trades: match each robot FillKey to a tagged QUIK trade (tag==robot, OrderNum match, qty equal, |price diff| within PriceStep); unmatched either direction → TradesOK=false (mismatch, no step — informational + it flips State).
  - Position: `RobotCheck.Position` = robot.Position (its belief), shown; NOT compared to account-net. `Report.Manual.AccountNet` = the raw Acc.Positions.
  - STALE gate unchanged (PosAgeMs/OrdAgeMs). Plan.ID hashing unchanged (Steps + PosAtMs/OrdAtMs). Deterministic sort of Manual.Orders (by OrderNum), RobotChecks (by ID), steps (existing).
  - Delete `Inputs.ManualOffset` and all its uses.

- [ ] **Step 3: Run full recon suite to PASS, commit**

```bash
git add quik_agent/internal/recon/
git commit -m "feat(recon): tag-based robot attribution; manual shown never aligned; drop manual_offset"
```

---

### Task 4: status wiring + page — feed tags, manual block, robot self-consistency, remove manual-offset

**Files:**
- Modify: `quik_agent/internal/status/status.go` (buildReconInputs ~182, JSON builders, Deps: drop ManualGet/ManualSet), `quik_agent/internal/status/server.go` (drop `/api/manual-offset`), `quik_agent/internal/status/page.html`, `frontend/public/agent-status.html`
- Test: `quik_agent/internal/status/status_test.go`, `quik_agent/internal/status/server_test.go`

**Interfaces:**
- Consumes: Task 3 recon types (RobotView.Tag, Order/Trade.Tag, Report.Manual, RobotChecks).
- Produces: `/api/status` JSON `recon` block gains `manual: {orders: [...], account_net: [...]}` and `robot_checks: [...]`; drops `manual_offsets`. Deps loses `ManualGet`/`ManualSet`.

- [ ] **Step 1: Failing status_test** — a fixture with a manual (untagged) order + a robot order (tagged) asserts, through the marshaled `/api/status` JSON, that `recon.manual.orders` holds the untagged one, the robot order is not MANUAL, and `recon.manual_offsets` is GONE.
- [ ] **Step 2: FAIL, implement.** In buildReconInputs: set each RobotView.Tag = its ID; copy `o.Tag`/`t.Tag` into recon.Order/Trade; remove the manual-offset wiring (ManualGet). Build the JSON manual + robot_checks blocks. Remove `ManualGet`/`ManualSet` from Deps and the `/api/manual-offset` route + handler (server.go). Remove the manual-offset editor from both page files.
- [ ] **Step 3: PASS. Page: split recon into "Мои роботы" (robot_checks + robot orders/trades) and "Ручная торговля (не сверяется)" (manual.orders + manual.account_net).** Apply to `page.html`, then copy verbatim to `frontend/public/agent-status.html` (keep the SOURCE OF TRUTH comment line 1). Remove the manual-offset input.
- [ ] **Step 4: PASS all status tests, commit**

```bash
git add quik_agent/internal/status/ frontend/public/agent-status.html
git commit -m "feat(showcase): Мои роботы vs Ручная торговля sections; drop manual-offset"
```

---

### Task 5: runner.Server relay — SendSetParams + SendDeploy

**Files:**
- Modify: `quik_agent/internal/runner/server.go`
- Test: `quik_agent/internal/runner/server_test.go`

**Interfaces:**
- Produces: `func (s *Server) SendSetParams(robotID, paramsJSON string) error` and `func (s *Server) SendDeploy(spec *quikv1.RobotSpec) error`, each pushing a `RunnerControl{set_params|deploy}` into the control channel exactly like the existing `SendFixState` (Task 8 of the showcase plan) / the link's deploy relay. Return an error when no runner is attached (so a GUI mode flip surfaces "runner offline").

- [ ] **Step 1: Failing test** — build the Server with a connected fake control stream (reuse `server_test.go` fixtures), call `SendDeploy(spec)` and `SendSetParams(id, json)`, assert the control frames arrive; call with no runner attached → error.
- [ ] **Step 2: FAIL, implement** mirroring `SendFixState`'s channel push. **Step 3: PASS, commit** — `git commit -m "feat(agent): runner relay SendSetParams + SendDeploy for GUI control"`

---

### Task 6: agent-local endpoints — POST /api/robot/{id}/params and /api/robot/{id}/mode

**Files:**
- Modify: `quik_agent/internal/status/server.go` (routes ~56), `quik_agent/internal/status/status.go` (Deps)
- Test: `quik_agent/internal/status/server_test.go`

**Interfaces:**
- Produces: `Deps.ParamsSet func(id string, upd ParamsUpdate) error` and `Deps.ModeSet func(id string, paper bool, confirmID string) error`, where `type ParamsUpdate struct { ParamsJSON *string; Schedule *string; MaxPosition *int64 }` (nil = unchanged). Routes: `POST /api/robot/{id}/params` (calls ParamsSet; 400 on bad JSON/range, 404 unknown id via a sentinel error, 200 ok) and `POST /api/robot/{id}/mode` (calls ModeSet; 409 with the reason string on a precondition/confirm failure, 404 unknown, 200 ok). Both parse `id` from the path. ModeSet returning a typed `ErrNotFlat`/`ErrConfirmMismatch`-style message maps to 409 with that text.

- [ ] **Step 1: Failing server tests** (httptest): `POST /api/robot/r1/params {"max_position":2}` calls ParamsSet with MaxPosition=2; malformed body → 400. `POST /api/robot/r1/mode {"paper":false,"confirm_id":"r1"}` calls ModeSet; a ModeSet returning a non-nil precondition error → 409 with the message; `confirm_id` empty/mismatch is the ModeSet's concern (fake returns the mismatch error → assert 409). Assert the mode ROUTE exists here.
- [ ] **Step 2: FAIL, implement the two handlers + Deps fields.** ParamsSet handler: decode `{params_json?, schedule?, max_position?}` into `ParamsUpdate` (pointers for presence), call `d.ParamsSet`. ModeSet handler: decode `{paper bool, confirm_id string}`, call `d.ModeSet(id, paper, confirmID)`; nil→200, else 409 (or 404 if the error is the unknown-id sentinel).
- [ ] **Step 3: PASS. Invariant #2 test** — assert the STL side has no mode route (documented cross-check; the actual STL app is Task 8, which deliberately omits it — add a comment in server_test noting mode is agent-local-only). **Commit** — `git commit -m "feat(agent): local /api/robot/{id}/params + /mode endpoints"`

---

### Task 7: main.go wiring — ParamsSet + ModeSet (flat-gate + DeployRobot paper-flip)

**Files:**
- Modify: `quik_agent/cmd/quik-agent/main.go` (status.Deps assembly), `quik_agent/internal/robots/store.go` (spec mutators if needed)
- Test: `quik_agent/internal/robots/store_test.go` (spec param/mode update round-trip)

**Interfaces:**
- Consumes: `robots.Store`, `runner.Server.SendSetParams/SendDeploy/LastStatuses`, `trade.Manager.SnapshotWorking/PendingTransViews`.
- Produces: the two `Deps` funcs, wired.

- [ ] **Step 1: robots.Store helper test + impl** — add `func (s *Store) UpdateParams(id string, paramsJSON *string, schedule *string, maxPos *int64) (*quikv1.RobotSpec, error)` (mutates the persisted spec, sets ParamsUpdatedAt, returns the new spec; error if unknown id) and `func (s *Store) SetPaper(id string, paper bool) (*quikv1.RobotSpec, error)`. Test round-trip through `NewStore` reload.
- [ ] **Step 2: Wire ParamsSet in main.go** — `ParamsSet: func(id string, upd status.ParamsUpdate) error { spec, err := robotStore.UpdateParams(id, upd.ParamsJSON, upd.Schedule, upd.MaxPosition); if err != nil { return err }; return runnerSrv.SendSetParams(id, spec.GetParamsJson()) }`.
- [ ] **Step 3: Wire ModeSet in main.go with the flat gate:**

```go
ModeSet: func(id string, paper bool, confirmID string) error {
	spec := robotStore.Get(id)
	if spec == nil { return status.ErrUnknownRobot }
	if confirmID != id { return fmt.Errorf("подтверждение не совпадает: введите точный ID робота") }
	// FLAT gate (both directions): position 0, no working orders, no unresolved trans.
	if st, ok := runnerSrv.LastStatuses()[id]; ok && st.GetPosition() != 0 {
		return fmt.Errorf("робот не в нуле (позиция %d): закрой позицию перед сменой режима", st.GetPosition())
	}
	// SnapshotWorking includes UNacked (pending, OrderNum=="") robot orders too, so this
	// single check covers "no working orders AND nothing in flight" — no separate pending
	// -trans check needed.
	for _, ws := range mgr.SnapshotWorking() {
		if rid, ok := trade.RobotIDFromClientID(ws.ClientID); ok && rid == id {
			ref := ws.OrderNum
			if ref == "" { ref = "(в полёте)" }
			return fmt.Errorf("у робота есть активная заявка %s: сними её перед сменой режима", ref)
		}
	}
	newSpec, err := robotStore.SetPaper(id, paper)
	if err != nil { return err }
	return runnerSrv.SendDeploy(newSpec) // re-deploy flips paper on a flat book
},
```

(`status.ErrUnknownRobot` is an EXPORTED sentinel var in the status package; the handler maps `errors.Is(err, status.ErrUnknownRobot)` to 404. Uses `trade.RobotIDFromClientID` from Task 2.)
- [ ] **Step 4: Build+vet+full suite on the hoster** (`go build ./... && go vet ./... && go test ./...`). main.go has no unit test; it must compile + vet. **Commit** — `git commit -m "feat(agent): wire ParamsSet + ModeSet (flat-gate, DeployRobot paper-flip)"`

---

### Task 8: STL param relay endpoint

**Files:**
- Modify: `trader/api/quik_robots.py`
- Test: `tests/quik/test_robot_params_relay.py`

**Interfaces:**
- Produces: `POST /api/v1/quik/robots/{id}/params` (portal-authed) that enqueues a `SetRobotParams` on the agent's live session (reuse the existing command-enqueue path, e.g. as `deploy-agent`/self-update do). Body `{"params_json": "...", "schedule": "...", "max_position": N}` → build the `SetRobotParams` (params_json only, per the proto) and enqueue. NO mode endpoint here (invariant #2).

- [ ] **Step 1: Failing pytest** — post to the endpoint with a fake live session, assert a `SetRobotParams` command is enqueued for the agent with the given params_json. Reuse the `tests/quik/` fixture style.
- [ ] **Step 2: FAIL, implement** following `trader/api/quik_release.py`'s enqueue pattern (`server.enqueue_command(agent_id, cmd)`); `require_auth`. **Step 3: PASS + `ruff` + full unit suite, commit** — `git commit -m "feat(stl): relay robot params edit to the agent (no mode route)"`

---

### Task 9: Page — per-robot param editor + local-only guarded mode toggle

**Files:**
- Modify: `quik_agent/internal/status/page.html`, then copy to `frontend/public/agent-status.html`
- (Verification is manual + the go-live smoke; no JS test harness — keep logic tiny/auditable.)

**Interfaces:**
- Consumes: `/api/robot/{id}/params`, `/api/robot/{id}/mode` (local); the STL param endpoint (mirror); the `IS_LOCAL` flag already in the page (`SRC === '/api/status'`).

- [ ] **Step 1: Param editor.** Per robot row, an expander with inputs for the `params_json` fields (rendered from the current params object), `schedule`, `max_position`, and a Save button. Save POSTs `/api/robot/{id}/params` when `IS_LOCAL`, else the STL endpoint; re-poll on success; show the error text on failure. Escape all values via the existing `esc()`.
- [ ] **Step 2: Mode toggle (local-only).** Render the mode as a badge always. When `IS_LOCAL`, add a toggle that opens a guarded panel: auto-checked preconditions (position 0, no working orders, no unresolved trans — read from the robot's status JSON, shown green/red), the single-path checklist (a static reminder + a checkbox the operator ticks), and a text input to type the robot ID. The "Армировать в REAL" button is enabled ONLY when every precondition is green, the checkbox is ticked, and the typed ID equals the robot ID. It POSTs `/api/robot/{id}/mode {paper:false, confirm_id}`; on 409 show the returned reason. Real arming styled red/unmistakable. When NOT `IS_LOCAL`, show only the badge (no toggle).
- [ ] **Step 3: Copy verbatim** to `frontend/public/agent-status.html` (SOURCE OF TRUTH comment line 1), rebuild the frontend locally (`cd frontend && node ./node_modules/vite/bin/vite.js build`), confirm `dist/agent-status.html` exists. **Commit** — `git commit -m "feat(showcase): robot param editor + local-only guarded paper/real toggle"`

---

### Task 10: Deploy + runbook + go-live smoke wiring

**Files:**
- Modify: `docs/runbooks/quik-robot-agent-rollout.md`, `CLAUDE.md` (one line: tag model + GUI control)

- [ ] **Step 1: Runbook** — add: the new agent_config has no `recon_manual_offset` (retired); param editing from the local page or the STL mirror; the paper/real toggle is LOCAL console only, refuses a non-flat robot, needs the typed ID; the go-live first-real-order smoke now also verifies the order's `brokerref` shows the robot ID and that a concurrent manual order stays in "Ручная торговля".
- [ ] **Step 2: Deploy** — hoster `go test ./...` green; `bash ~/quik_build/publish_quik_agent.sh` (numeric build_rev; runner exe + strategies_doc.json staged) then trigger self-update on `quik-agent`; scp `frontend/dist/agent-status.html` to the hoster; `git push` + hoster `git pull` (+ `sudo systemctl restart shectory-trader` for the STL param endpoint, NOT during live trading); operator reloads the new `shectory_trade.lua` on the VDS.
- [ ] **Step 3: Verify live** — mirror JSON shows the "Мои роботы"/"Ручная торговля" split; the manual +15/6 orders now sit in "Ручная торговля" and recon State is OK (no robot findings); RTT/drift/lag still healthy. **Commit** — `git commit -m "docs: rollout runbook + CLAUDE.md for robot tagging + GUI control"`

---

## Execution notes

- Task order is dependency order. Tasks 1-4 (tag model) unblock the recon footgun (manual orders currently produce align steps) and should ship first; Tasks 5-9 (GUI control) build on the clean recon.
- The COMMENT→brokerref round-trip and the mode flip's real effect can only be fully verified with a REAL order (paper never reaches QUIK) — that verification is the go-live first-order smoke, not a unit test.
- Preserve every prior fix: Lua `%.0f`/empty-array/keepalive, accounts recv-vs-content stamp split, RTT agent-clock, page fmtAge, align idempotency latch + per-step client_id.
