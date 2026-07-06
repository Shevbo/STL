// Package status builds the agent's local operator showcase: a single JSON
// snapshot (agent/health/robots/recon) and an embedded HTML page that polls it.
// It is READ-ONLY except for three operator (mutating) actions: confirming a
// recon align plan (POST /api/align, Deps.AlignExec), editing a deployed
// robot's spec (POST /api/robot/{id}/params, Deps.ParamsSet), and flipping a
// robot between paper and real (POST /api/robot/{id}/mode, Deps.ModeSet — the
// real-money arming action, local-only, never mirrored to STL). No strategy/
// network logic lives here; this package only reads other packages'
// already-computed snapshots and relays these three actions to their wired
// executors.
package status

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"sort"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/recon"
	"shectory/quik_agent/internal/trade"

	quikv1 "shectory/quik_agent/internal/pb"
)

// ErrUnknownRobot is the sentinel Deps.ParamsSet/Deps.ModeSet implementations
// (wired in a later task) return when id names no known robot. The HTTP
// handlers in server.go map it to 404 via errors.Is; any other non-nil error
// is a precondition/validation failure and maps to 400 (params) or 409 (mode).
var ErrUnknownRobot = errors.New("unknown robot")

// ---- narrow, verbatim-named consumer interfaces ----
//
// Deps names the fields the brief specifies (Accounts/Robots/Runner/Manager/
// Provider) so later tasks' wiring code (`d.Accounts = accStore`, ...) compiles
// unchanged. The FIELD TYPES here are narrow interfaces over the exact methods
// this package calls, not the concrete structs, so BuildStatus is unit-testable
// with small fakes instead of a live QUIK/runner/account stack. Every concrete
// type already satisfies its interface implicitly (Go structural typing), so
// assigning *accounts.Store / *robots.Store / *runner.Server / *trade.Manager /
// *quikdde.Provider into these fields requires no change at the call site.

// accountsSnapshotter is accounts.Store's read surface.
type accountsSnapshotter interface {
	Snapshot() accounts.Snapshot
}

// robotsStore is robots.Store's read surface.
type robotsStore interface {
	All() []*quikv1.RobotSpec
	Paused(robotID string) bool
	Times(robotID string) (deployedMs, paramsMs int64)
}

// runnerServer is runner.Server's read surface.
type runnerServer interface {
	LastStatuses() map[string]*quikv1.RobotStatus
	LastReportAgeMs() int64
	RunnerHealthy() bool
}

// tradeManager is trade.Manager's read surface.
type tradeManager interface {
	SnapshotWorking() []trade.WorkingSnapshot
	PendingTransViews() []trade.PendingTransView
}

// tickProvider is quikdde.Provider's read surface (tick freshness + params).
type tickProvider interface {
	Ticks() []quikdde.Tick
	Params() []quikdde.ParamRow
}

// ---- Deps ----

// StepResult is one align-plan step's execution outcome, as Deps.AlignExec
// (Task 8) returns it. Fields mirror recon.Step's identifying data (RobotID
// intentionally omitted vs. included per Step.Kind — see recon.Step) plus the
// per-step outcome; SetPos/SetAvg are omitted here as align-time inputs, not
// execution outputs.
type StepResult struct {
	Kind     string `json:"kind"`
	Symbol   string `json:"symbol"`
	OrderNum string `json:"order_num,omitempty"`
	RobotID  string `json:"robot_id,omitempty"`
	Detail   string `json:"detail"`
	OK       bool   `json:"ok"`
	Error    string `json:"error,omitempty"`
}

// StepResultFrom builds a StepResult from the recon.Step it executed, for
// Deps.AlignExec implementations (Task 8) to use as a convenience constructor.
func StepResultFrom(step recon.Step, ok bool, errMsg string) StepResult {
	return StepResult{
		Kind:     step.Kind,
		Symbol:   step.Symbol,
		OrderNum: step.OrderNum,
		RobotID:  step.RobotID,
		Detail:   step.Detail,
		OK:       ok,
		Error:    errMsg,
	}
}

// ParamsUpdate is a partial edit to a robot's spec: a nil pointer field means
// "leave unchanged" (Deps.ParamsSet applies only the non-nil fields).
type ParamsUpdate struct {
	ParamsJSON  *string
	Schedule    *string
	MaxPosition *int64
}

// Deps is everything BuildStatus/NewServer need. The FUNC fields are called
// only if non-nil (nil-safe defaults below); the five interface fields
// (Accounts/Robots/Runner/Manager/Provider) have no such guard and MUST be
// set — BuildStatus reads them unconditionally.
type Deps struct {
	Accounts accountsSnapshotter
	Robots   robotsStore
	Runner   runnerServer
	Manager  tradeManager
	Provider tickProvider

	LinkUp     func() bool
	Reconnects func() uint32
	UptimeSec  func() int64
	MasterFlag bool
	BuildRev   uint32
	Version    string

	AlignExec func(plan recon.Plan) []StepResult // Task 8

	// ParamsSet applies a partial robot spec edit (POST /api/robot/{id}/params).
	// Returns ErrUnknownRobot for an unknown id; any other error is a
	// validation/range failure (Task 7).
	ParamsSet func(id string, upd ParamsUpdate) error

	// ModeSet flips a robot between paper and real (POST /api/robot/{id}/mode),
	// the local-only real-money arming action. confirmID is the operator's
	// typed confirmation (must match id per the caller's own policy). Returns
	// ErrUnknownRobot for an unknown id; any other error is a
	// precondition/confirm failure whose text is shown to the operator (Task 7).
	ModeSet func(id string, paper bool, confirmID string) error

	LogPaths map[string]string // "agent"/"runner" -> file path
	DocsPath string            // strategies_doc.json (Task 10)
	NowMs    func() int64
}

func (d Deps) linkUp() bool {
	if d.LinkUp == nil {
		return false
	}
	return d.LinkUp()
}

func (d Deps) reconnects() uint32 {
	if d.Reconnects == nil {
		return 0
	}
	return d.Reconnects()
}

func (d Deps) uptimeSec() int64 {
	if d.UptimeSec == nil {
		return 0
	}
	return d.UptimeSec()
}

func (d Deps) nowMs() int64 {
	if d.NowMs == nil {
		return 0
	}
	return d.NowMs()
}

// buildReconInputs adapts every Deps source into recon.Inputs. Attribution is by tag
// (recon classifies each Acc order/trade by its brokerref Tag), so each RobotView carries
// its ID as its Tag and the Acc order/trade tags are copied through verbatim.
func buildReconInputs(d Deps) recon.Inputs {
	acc := d.Accounts.Snapshot()

	accView := recon.AccView{
		PosAgeMs: acc.PosAgeMs,
		OrdAgeMs: acc.OrdAgeMs,
		PosAtMs:  acc.PosAtMs,
		OrdAtMs:  acc.OrdAtMs,
	}
	for _, p := range acc.Positions {
		accView.Positions = append(accView.Positions, recon.Position{Sec: p.Sec, Net: p.Net, Avg: p.Avg})
	}
	for _, o := range acc.Orders {
		accView.Orders = append(accView.Orders, recon.Order{
			Num: o.Num, Sec: o.Sec, Active: o.Active, Price: o.Price, Balance: o.Balance, Qty: o.Qty, Tag: o.Tag,
		})
	}
	for _, t := range acc.Trades {
		accView.Trades = append(accView.Trades, recon.Trade{
			Num: t.Num, OrderNum: t.OrderNum, Sec: t.Sec, Price: t.Price, Qty: t.Qty, TsMs: t.TsMs, Tag: t.Tag,
		})
	}

	// Robot-owned working orders, grouped by robot ID (client_id "rr:<robotID>:n"), feed
	// each RobotView's OrderNums so recon can flag a MISSING (believed-working-but-absent)
	// or a ROBOT_ORPHAN (tagged-but-unknown) order. A non-robot client_id is the
	// human/manual path; under the tag model its QUIK order carries an empty/unknown
	// brokerref and recon attributes it as MANUAL, so nothing extra is tracked here.
	robotOrderNums := map[string][]string{}
	for _, ws := range d.Manager.SnapshotWorking() {
		if ws.OrderNum == "" {
			continue // not yet acknowledged by QUIK; nothing to reconcile against yet
		}
		if rid, ok := trade.RobotIDFromClientID(ws.ClientID); ok {
			robotOrderNums[rid] = append(robotOrderNums[rid], ws.OrderNum)
		}
	}

	var trans []recon.TransCheck
	for _, tv := range d.Manager.PendingTransViews() {
		trans = append(trans, recon.TransCheck{TransID: tv.TransID, Status: tv.Status, Text: tv.Text, OK: tv.OK})
	}

	priceStep := map[string]float64{}
	for _, pr := range d.Provider.Params() {
		if pr.PriceStep > 0 {
			priceStep[pr.Code] = pr.PriceStep
		}
	}

	lastStatuses := d.Runner.LastStatuses()
	var robotViews []recon.RobotView
	for _, spec := range d.Robots.All() {
		id := spec.GetRobotId()
		st := lastStatuses[id] // nil-safe: proto getters tolerate a nil receiver
		rv := recon.RobotView{
			ID:       id,
			Tag:      id, // the robot's brokerref == its ID (stamped into the order COMMENT)
			Symbol:   spec.GetSymbol(),
			Paper:    spec.GetPaper(),
			Position: st.GetPosition(),
			AvgPrice: st.GetAvgPrice(),
		}
		rv.OrderNums = append(rv.OrderNums, robotOrderNums[id]...)
		if !rv.Paper {
			for _, f := range st.GetRecentFills() {
				if f.GetStatus() != "filled" {
					continue // no QUIK trade exists for a rejected/skipped/paper fill
				}
				rv.FillKeys = append(rv.FillKeys, recon.FillKey{
					OrderNum: f.GetOrderId(), Qty: f.GetQty(), Price: f.GetPrice(),
				})
			}
		}
		robotViews = append(robotViews, rv)
	}

	return recon.Inputs{
		Robots:    robotViews,
		Acc:       accView,
		Trans:     trans,
		PriceStep: priceStep,
		NowMs:     d.nowMs(),
	}
}

// computeReport is the single place that turns Deps into a recon.Report, used
// by both BuildStatus and the /api/align handler so they can never disagree.
func computeReport(d Deps) recon.Report {
	return recon.Evaluate(buildReconInputs(d))
}

// EvaluateRecon runs the exact same computation BuildStatus uses and
// additionally reports whether the current MISMATCH (if any) involves at
// least one non-paper ("real") robot — either directly (a fix_state step
// names the robot) or via a symbol that robot trades (a cancel_order/
// close_position step names only a symbol). Wired into main.go's recon alert
// loop (Task 9, feeding ReconAlerter.Step) so alert severity can be decided
// without duplicating recon evaluation or reaching into recon.Report's
// internals from outside this package. "OK"/"STALE" always report
// realInvolved=false (there is nothing to be involved in).
//
// Deliberate narrowing (review-adjudicated): realInvolved is scoped to THIS
// mismatch's plan — a non-paper robot (or a symbol it trades) must appear in
// the plan's steps — NOT the looser "any real robot deployed anywhere", so a
// paper-only discrepancy never escalates to CRITICAL just because an
// unrelated real robot happens to be hosted on the same agent.
func EvaluateRecon(d Deps) (state string, realInvolved bool) {
	rep := computeReport(d)
	if rep.State != "MISMATCH" || rep.Plan == nil {
		return rep.State, false
	}
	realRobots := map[string]bool{}
	realSymbols := map[string]bool{}
	for _, spec := range d.Robots.All() {
		if spec.GetPaper() {
			continue
		}
		realRobots[spec.GetRobotId()] = true
		realSymbols[spec.GetSymbol()] = true
	}
	for _, s := range rep.Plan.Steps {
		if realRobots[s.RobotID] || realSymbols[s.Symbol] {
			return rep.State, true
		}
	}
	return rep.State, false
}

// sideString renders a quikv1.Side as the page's lowercase convention.
func sideString(s quikv1.Side) string {
	switch s {
	case quikv1.Side_SIDE_BUY:
		return "buy"
	case quikv1.Side_SIDE_SELL:
		return "sell"
	default:
		return ""
	}
}

// ---- wire (JSON) shapes ----

type agentJSON struct {
	Version    string `json:"version"`
	BuildRev   uint32 `json:"build_rev"`
	UptimeSec  int64  `json:"uptime_sec"`
	MasterFlag bool   `json:"master_flag"`
	LinkUp     bool   `json:"link_up"`
	Reconnects uint32 `json:"reconnects"`
}

type feedJSON struct {
	Code  string  `json:"code"`
	AgeMs int64   `json:"age_ms"`
	Last  float64 `json:"last"`
	Bid   float64 `json:"bid"`
	Ask   float64 `json:"ask"`
}

type healthJSON struct {
	Feed              []feedJSON `json:"feed"`
	RTTMs             int64      `json:"rtt_ms"`
	PongAgeMs         int64      `json:"pong_age_ms"`
	ClockDriftMs      int64      `json:"clock_drift_ms"`
	ExchangeLagMs     int64      `json:"exchange_lag_ms"`
	PosAgeMs          int64      `json:"pos_age_ms"`
	OrdAgeMs          int64      `json:"ord_age_ms"`
	RunnerHealthy     bool       `json:"runner_healthy"`
	RunnerReportAgeMs int64      `json:"runner_report_age_ms"`
}

type fillJSON struct {
	OrderID string  `json:"order_id"`
	Symbol  string  `json:"symbol"`
	Side    string  `json:"side"`
	Qty     int64   `json:"qty"`
	Price   float64 `json:"price"`
	Status  string  `json:"status"`
	TsMs    int64   `json:"ts_unix_ms"`
}

type workingOrderJSON struct {
	ClientID string  `json:"client_id"`
	OrderID  string  `json:"order_id"`
	Side     string  `json:"side"`
	Price    float64 `json:"price"`
	Qty      int64   `json:"qty"`
	State    string  `json:"state"`
	TsMs     int64   `json:"ts_unix_ms"`
}

type robotJSON struct {
	ID                string             `json:"id"`
	Symbol            string             `json:"symbol"`
	StrategyID        string             `json:"strategy_id"`
	Mode              string             `json:"mode"` // "paper" | "real"
	Paused            bool               `json:"paused"`
	Running           bool               `json:"running"`
	Schedule          string             `json:"schedule"`
	ParamsJSON        string             `json:"params_json"`
	MaxPosition       int64              `json:"max_position"`
	Position          int64              `json:"position"`
	AvgPrice          float64            `json:"avg_price"`
	PnlPoints         float64            `json:"pnl_points"`
	PnlRub            *float64           `json:"pnl_rub,omitempty"`
	LastBarUnix       int64              `json:"last_bar_unix"`
	HeartbeatUnixMs   int64              `json:"heartbeat_unix_ms"`
	BarsCount         int32              `json:"bars_count"`
	Note              string             `json:"note"`
	SignalJSON        string             `json:"signal_json"`
	RecentFills       []fillJSON         `json:"recent_fills"`
	WorkingOrders     []workingOrderJSON `json:"working_orders"`
	DeployedAtMs      int64              `json:"deployed_at_ms"`
	ParamsUpdatedAtMs int64              `json:"params_updated_at_ms"`
	HasStatus         bool               `json:"has_status"`
}

type orderCheckJSON struct {
	OrderNum string `json:"order_num"`
	Owner    string `json:"owner"`
	OK       bool   `json:"ok"`
}

type manualOrderJSON struct {
	OrderNum string `json:"order_num"`
	Sec      string `json:"sec"`
}

type posLineJSON struct {
	Sec string `json:"sec"`
	Net int64  `json:"net"`
}

type manualViewJSON struct {
	Orders     []manualOrderJSON `json:"orders"`
	AccountNet []posLineJSON     `json:"account_net"`
}

type robotCheckJSON struct {
	ID       string `json:"id"`
	Symbol   string `json:"symbol"`
	Position int64  `json:"position"`
	OrdersOK bool   `json:"orders_ok"`
	TradesOK bool   `json:"trades_ok"`
}

type tradeCheckJSON struct {
	TradeID  string `json:"trade_id"`
	OrderNum string `json:"order_num"`
	Matched  bool   `json:"matched"`
}

type transCheckJSON struct {
	TransID int64  `json:"trans_id"`
	Status  string `json:"status"`
	Text    string `json:"text"`
	OK      bool   `json:"ok"`
}

type stepJSON struct {
	Kind     string  `json:"kind"`
	Detail   string  `json:"detail"`
	Symbol   string  `json:"symbol"`
	OrderNum string  `json:"order_num,omitempty"`
	Qty      int64   `json:"qty,omitempty"`
	RobotID  string  `json:"robot_id,omitempty"`
	SetPos   int64   `json:"set_pos,omitempty"`
	SetAvg   float64 `json:"set_avg,omitempty"`
}

type planJSON struct {
	ID    string     `json:"id"`
	Steps []stepJSON `json:"steps"`
}

type reconJSON struct {
	State       string           `json:"state"`
	Orders      []orderCheckJSON `json:"orders"`
	Trades      []tradeCheckJSON `json:"trades"`
	Trans       []transCheckJSON `json:"trans"`
	Manual      manualViewJSON   `json:"manual"`
	RobotChecks []robotCheckJSON `json:"robot_checks"`
	Plan        *planJSON        `json:"plan"`
}

type statusJSON struct {
	Agent  agentJSON   `json:"agent"`
	Health healthJSON  `json:"health"`
	Robots []robotJSON `json:"robots"`
	Recon  reconJSON   `json:"recon"`
}

func toPlanJSON(p *recon.Plan) *planJSON {
	if p == nil {
		return nil
	}
	steps := make([]stepJSON, 0, len(p.Steps))
	for _, s := range p.Steps {
		steps = append(steps, stepJSON{
			Kind: s.Kind, Detail: s.Detail, Symbol: s.Symbol, OrderNum: s.OrderNum,
			Qty: s.Qty, RobotID: s.RobotID, SetPos: s.SetPos, SetAvg: s.SetAvg,
		})
	}
	return &planJSON{ID: p.ID, Steps: steps}
}

func toReconJSON(rep recon.Report) reconJSON {
	out := reconJSON{State: rep.State}
	for _, o := range rep.Orders {
		out.Orders = append(out.Orders, orderCheckJSON{OrderNum: o.OrderNum, Owner: o.Owner, OK: o.OK})
	}
	for _, t := range rep.Trades {
		out.Trades = append(out.Trades, tradeCheckJSON{TradeID: t.TradeID, OrderNum: t.OrderNum, Matched: t.Matched})
	}
	for _, t := range rep.Trans {
		out.Trans = append(out.Trans, transCheckJSON{TransID: t.TransID, Status: t.Status, Text: t.Text, OK: t.OK})
	}

	// Manual (untagged / unknown-tag) block: shown for context, never reconciled (the
	// "Ручная торговля (не сверяется)" UI section). Empty slices, not null.
	out.Manual = manualViewJSON{Orders: []manualOrderJSON{}, AccountNet: []posLineJSON{}}
	for _, m := range rep.Manual.Orders {
		out.Manual.Orders = append(out.Manual.Orders, manualOrderJSON{OrderNum: m.OrderNum, Sec: m.Sec})
	}
	for _, p := range rep.Manual.AccountNet {
		out.Manual.AccountNet = append(out.Manual.AccountNet, posLineJSON{Sec: p.Sec, Net: p.Net})
	}

	// Per-robot self-consistency summary ("Мои роботы").
	out.RobotChecks = []robotCheckJSON{}
	for _, rc := range rep.RobotChecks {
		out.RobotChecks = append(out.RobotChecks, robotCheckJSON{
			ID: rc.ID, Symbol: rc.Symbol, Position: rc.Position, OrdersOK: rc.OrdersOK, TradesOK: rc.TradesOK,
		})
	}

	out.Plan = toPlanJSON(rep.Plan)
	return out
}

func buildAgentJSON(d Deps) agentJSON {
	return agentJSON{
		Version:    d.Version,
		BuildRev:   d.BuildRev,
		UptimeSec:  d.uptimeSec(),
		MasterFlag: d.MasterFlag,
		LinkUp:     d.linkUp(),
		Reconnects: d.reconnects(),
	}
}

func buildHealthJSON(d Deps, acc accounts.Snapshot) healthJSON {
	h := healthJSON{
		RTTMs:             acc.RTTMs,
		PongAgeMs:         acc.PongAgeMs,
		ClockDriftMs:      acc.ClockDriftMs,
		ExchangeLagMs:     acc.ExchangeLagMs,
		PosAgeMs:          acc.PosAgeMs,
		OrdAgeMs:          acc.OrdAgeMs,
		RunnerHealthy:     d.Runner.RunnerHealthy(),
		RunnerReportAgeMs: d.Runner.LastReportAgeMs(),
	}
	now := d.nowMs()
	ticks := d.Provider.Ticks()
	sort.Slice(ticks, func(i, j int) bool { return ticks[i].Code < ticks[j].Code })
	for _, t := range ticks {
		h.Feed = append(h.Feed, feedJSON{
			Code: t.Code, AgeMs: now - t.ReceivedUnixMs, Last: t.Last, Bid: t.Bid, Ask: t.Ask,
		})
	}
	return h
}

func buildRobotsJSON(d Deps) []robotJSON {
	lastStatuses := d.Runner.LastStatuses()
	paramsBySymbol := map[string]quikdde.ParamRow{}
	for _, pr := range d.Provider.Params() {
		paramsBySymbol[pr.Code] = pr
	}

	specs := d.Robots.All()
	sort.Slice(specs, func(i, j int) bool { return specs[i].GetRobotId() < specs[j].GetRobotId() })

	out := make([]robotJSON, 0, len(specs))
	for _, spec := range specs {
		id := spec.GetRobotId()
		st, hasStatus := lastStatuses[id]
		deployedMs, paramsMs := d.Robots.Times(id)

		mode := "real"
		if spec.GetPaper() {
			mode = "paper"
		}

		rj := robotJSON{
			ID:                id,
			Symbol:            spec.GetSymbol(),
			StrategyID:        spec.GetStrategyId(),
			Mode:              mode,
			Paused:            d.Robots.Paused(id),
			Running:           st.GetRunning(),
			Schedule:          spec.GetSchedule(),
			ParamsJSON:        spec.GetParamsJson(),
			MaxPosition:       spec.GetMaxPositionContracts(),
			Position:          st.GetPosition(),
			AvgPrice:          st.GetAvgPrice(),
			PnlPoints:         st.GetRealizedPnl(),
			LastBarUnix:       st.GetLastBarUnix(),
			HeartbeatUnixMs:   st.GetHeartbeatUnixMs(),
			BarsCount:         st.GetBarsCount(),
			Note:              st.GetNote(),
			SignalJSON:        st.GetSignalJson(),
			DeployedAtMs:      deployedMs,
			ParamsUpdatedAtMs: paramsMs,
			HasStatus:         hasStatus,
		}

		// Rubles are emitted only when BOTH params are genuinely known (> 0):
		// the DDE-sheet fallback parse can yield a row with PriceStep set but
		// StepCost 0, and a fabricated "pnl_rub": 0 would be a lie.
		if pr, ok := paramsBySymbol[rj.Symbol]; ok && pr.PriceStep > 0 && pr.StepCost > 0 {
			coef := pr.StepCost / pr.PriceStep
			rub := rj.PnlPoints * coef
			rj.PnlRub = &rub
		}

		for _, f := range st.GetRecentFills() {
			rj.RecentFills = append(rj.RecentFills, fillJSON{
				OrderID: f.GetOrderId(), Symbol: f.GetSymbol(), Side: sideString(f.GetSide()),
				Qty: f.GetQty(), Price: f.GetPrice(), Status: f.GetStatus(), TsMs: f.GetTsUnixMs(),
			})
		}
		for _, w := range st.GetWorkingOrders() {
			rj.WorkingOrders = append(rj.WorkingOrders, workingOrderJSON{
				ClientID: w.GetClientId(), OrderID: w.GetOrderId(), Side: sideString(w.GetSide()),
				Price: w.GetPrice(), Qty: w.GetQty(), State: w.GetState(), TsMs: w.GetTsUnixMs(),
			})
		}

		out = append(out, rj)
	}
	return out
}

// BuildStatus renders the full /api/status JSON snapshot: agent/health/robots
// top-level keys are read straight off Deps' sources; recon comes from the
// same computeReport() the /api/align handler recomputes against, so a
// confirm can never race a picture the page never saw.
func BuildStatus(d Deps) ([]byte, error) {
	acc := d.Accounts.Snapshot()
	rep := computeReport(d)

	out := statusJSON{
		Agent:  buildAgentJSON(d),
		Health: buildHealthJSON(d, acc),
		Robots: buildRobotsJSON(d),
		Recon:  toReconJSON(rep),
	}
	return json.Marshal(out)
}

// GateHash returns a change-detection digest of a /api/status JSON payload (as produced
// by BuildStatus) with fields that legitimately drift every tick REGARDLESS of any real
// change — uptime, every per-instrument tick age, the pos/ord/runner-report table ages,
// the pong RTT, and the exchange lag (now recomputed against the wall clock on every 5s
// ping even with no new trade — see accounts.Store.SetPong) — zeroed out first.
//
// Without this, link.Link.maybeSendStatusSnapshot's change-gate would never actually
// gate anything: the JSON differs every single heartbeat purely from these counters
// ticking upward, so STL would get pushed on every tick no matter how quiet the
// account/robots actually are. The bytes the caller actually SENDS to STL are the
// original, untouched `data` — this function only decides WHETHER to send.
func GateHash(data []byte) ([32]byte, error) {
	var v statusJSON
	if err := json.Unmarshal(data, &v); err != nil {
		return [32]byte{}, err
	}
	v.Agent.UptimeSec = 0
	v.Health.RTTMs = 0
	v.Health.PongAgeMs = 0
	v.Health.ExchangeLagMs = 0
	v.Health.PosAgeMs = 0
	v.Health.OrdAgeMs = 0
	v.Health.RunnerReportAgeMs = 0
	for i := range v.Health.Feed {
		v.Health.Feed[i].AgeMs = 0
	}
	gateData, err := json.Marshal(v)
	if err != nil {
		return [32]byte{}, err
	}
	return sha256.Sum256(gateData), nil
}
