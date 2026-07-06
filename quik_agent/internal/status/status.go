// Package status builds the agent's local operator showcase: a single JSON
// snapshot (agent/health/robots/recon) and an embedded HTML page that polls it.
// It is READ-ONLY except for two operator actions: confirming a recon align
// plan (POST /api/align, executed by Deps.AlignExec — wired in a later task)
// and editing the manual position offset (POST /api/manual-offset). No
// strategy/network logic lives here; this package only reads other packages'
// already-computed snapshots and renders them.
package status

import (
	"encoding/json"
	"sort"
	"strings"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/recon"
	"shectory/quik_agent/internal/trade"

	quikv1 "shectory/quik_agent/internal/pb"
)

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

	ManualGet func() map[string]int64            // from config
	ManualSet func(map[string]int64) error       // persists to agent_config.json
	AlignExec func(plan recon.Plan) []StepResult // Task 8

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

func (d Deps) manualOffsets() map[string]int64 {
	if d.ManualGet == nil {
		return map[string]int64{}
	}
	m := d.ManualGet()
	if m == nil {
		return map[string]int64{}
	}
	return m
}

func (d Deps) nowMs() int64 {
	if d.NowMs == nil {
		return 0
	}
	return d.NowMs()
}

// robotIDFromClientID extracts the robot ID from a runner-owned client_id of
// the form "rr:<robotID>:<n>" (see runner.Server's runnerPrefix). Returns
// ok=false for a client_id that is not runner-owned (the human order path).
func robotIDFromClientID(clientID string) (string, bool) {
	const prefix = "rr:"
	if !strings.HasPrefix(clientID, prefix) {
		return "", false
	}
	rest := clientID[len(prefix):]
	if i := strings.Index(rest, ":"); i >= 0 {
		return rest[:i], true
	}
	return rest, true
}

// buildReconInputs adapts every Deps source into recon.Inputs, exactly as the
// package doc for recon.Inputs specifies (Robots/HumanOrders/Acc/Trans/
// ManualOffset/PriceStep/NowMs).
func buildReconInputs(d Deps) recon.Inputs {
	acc := d.Accounts.Snapshot()

	accView := recon.AccView{
		PosAgeMs: acc.PosAgeMs,
		OrdAgeMs: acc.OrdAgeMs,
	}
	for _, p := range acc.Positions {
		accView.Positions = append(accView.Positions, recon.Position{Sec: p.Sec, Net: p.Net, Avg: p.Avg})
	}
	for _, o := range acc.Orders {
		accView.Orders = append(accView.Orders, recon.Order{
			Num: o.Num, Sec: o.Sec, Active: o.Active, Price: o.Price, Balance: o.Balance, Qty: o.Qty,
		})
	}
	for _, t := range acc.Trades {
		accView.Trades = append(accView.Trades, recon.Trade{
			Num: t.Num, OrderNum: t.OrderNum, Sec: t.Sec, Price: t.Price, Qty: t.Qty, TsMs: t.TsMs,
		})
	}

	// Working orders, split into robot-owned (grouped by robot ID, "rr:" prefix)
	// and human-owned (everything else), matching recon.Inputs' doc exactly.
	robotOrderNums := map[string][]string{}
	humanOrders := map[string]bool{}
	for _, ws := range d.Manager.SnapshotWorking() {
		if ws.OrderNum == "" {
			continue // not yet acknowledged by QUIK; nothing to reconcile against yet
		}
		if rid, ok := robotIDFromClientID(ws.ClientID); ok {
			robotOrderNums[rid] = append(robotOrderNums[rid], ws.OrderNum)
		} else {
			humanOrders[ws.OrderNum] = true
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
		Robots:       robotViews,
		HumanOrders:  humanOrders,
		Acc:          accView,
		Trans:        trans,
		ManualOffset: d.manualOffsets(),
		PriceStep:    priceStep,
		NowMs:        d.nowMs(),
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

type posCheckJSON struct {
	Symbol       string `json:"symbol"`
	RobotsSum    int64  `json:"robots_sum"`
	Quik         int64  `json:"quik"`
	ManualOffset int64  `json:"manual_offset"`
	OK           bool   `json:"ok"`
}

type orderCheckJSON struct {
	OrderNum string `json:"order_num"`
	Owner    string `json:"owner"`
	OK       bool   `json:"ok"`
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
	State         string           `json:"state"`
	Positions     []posCheckJSON   `json:"positions"`
	Orders        []orderCheckJSON `json:"orders"`
	Trades        []tradeCheckJSON `json:"trades"`
	Trans         []transCheckJSON `json:"trans"`
	Plan          *planJSON        `json:"plan"`
	ManualOffsets map[string]int64 `json:"manual_offsets"`
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

func toReconJSON(rep recon.Report, manualOffsets map[string]int64) reconJSON {
	out := reconJSON{State: rep.State, ManualOffsets: manualOffsets}
	for _, p := range rep.Positions {
		out.Positions = append(out.Positions, posCheckJSON{
			Symbol: p.Symbol, RobotsSum: p.RobotsSum, Quik: p.Quik, ManualOffset: p.ManualOffset, OK: p.OK,
		})
	}
	for _, o := range rep.Orders {
		out.Orders = append(out.Orders, orderCheckJSON{OrderNum: o.OrderNum, Owner: o.Owner, OK: o.OK})
	}
	for _, t := range rep.Trades {
		out.Trades = append(out.Trades, tradeCheckJSON{TradeID: t.TradeID, OrderNum: t.OrderNum, Matched: t.Matched})
	}
	for _, t := range rep.Trans {
		out.Trans = append(out.Trans, transCheckJSON{TransID: t.TransID, Status: t.Status, Text: t.Text, OK: t.OK})
	}
	out.Plan = toPlanJSON(rep.Plan)
	if out.ManualOffsets == nil {
		out.ManualOffsets = map[string]int64{}
	}
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
		Recon:  toReconJSON(rep, d.manualOffsets()),
	}
	return json.Marshal(out)
}
