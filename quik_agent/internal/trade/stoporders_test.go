package trade

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

type stopBridge struct {
	seq  int64
	sent []stopTxCmd
}

func (b *stopBridge) NextTransID() int64       { b.seq++; return b.seq }
func (b *stopBridge) Place(placeCmd) error     { return nil }
func (b *stopBridge) Cancel(cancelCmd) error   { return nil }
func (b *stopBridge) Move(moveCmd) error       { return nil }
func (b *stopBridge) StopTx(c stopTxCmd) error { b.sent = append(b.sent, c); return nil }

type stopEmit struct {
	replies []*quikv1.TransReply
	reports []*quikv1.StopOrderReport
}

func (e *stopEmit) EmitOrderUpdate(*quikv1.OrderUpdate) error { return nil }
func (e *stopEmit) EmitTransReply(r *quikv1.TransReply) error {
	e.replies = append(e.replies, r)
	return nil
}
func (e *stopEmit) EmitExecutionUpdate(*quikv1.ExecutionUpdate) error { return nil }
func (e *stopEmit) EmitAlert(quikv1.AlertSeverity, string, string) error {
	return nil
}
func (e *stopEmit) EmitStopOrderReport(r *quikv1.StopOrderReport) error {
	e.reports = append(e.reports, r)
	return nil
}

func stopMgr(enabled bool) (*Manager, *stopBridge, *stopEmit) {
	b, e := &stopBridge{}, &stopEmit{}
	g := NewGuard(Limits{TradingEnabled: enabled, InstrumentWhitelist: []string{"GZU6"},
		MaxContractsPerOrder: 1, MaxWorkingContracts: 1, PriceCollarFrac: 0.002, DailyOrderCap: 10})
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT", Account: "ACC1"}, b, g, e, nil)
	return m, b, e
}

func gzStop(qty int64, fields map[string]string) *quikv1.PlaceStopOrder {
	return &quikv1.PlaceStopOrder{ClientId: "so:0123456789", Code: "GZU6",
		Side: quikv1.Side_SIDE_SELL, Quantity: qty, Fields: fields}
}

// The agent stamps where and how much; the map carries only the kind-specific fields.
func TestPlaceStopOrderStampsFrameAndTag(t *testing.T) {
	m, b, _ := stopMgr(true)
	if err := m.PlaceStopOrderErr(gzStop(1, map[string]string{
		"stop_order_kind": "SIMPLE_STOP_ORDER", "STOPPRICE": "12340"})); err != nil {
		t.Fatalf("place: %v", err)
	}
	if len(b.sent) != 1 {
		t.Fatalf("sent %d", len(b.sent))
	}
	c := b.sent[0]
	want := map[string]string{"ACTION": "NEW_STOP_ORDER", "CLASSCODE": "SPBFUT", "SECCODE": "GZU6",
		"OPERATION": "S", "QUANTITY": "1", "ACCOUNT": "ACC1",
		"STOP_ORDER_KIND": "SIMPLE_STOP_ORDER", "STOPPRICE": "12340"}
	for k, v := range want {
		if c.Fields[k] != v {
			t.Fatalf("field %s=%q want %q (all %v)", k, c.Fields[k], v, c.Fields)
		}
	}
	if c.Comment != "stl-so-0123456789" {
		t.Fatalf("tag %q", c.Comment)
	}
}

// Real money: the gate is the same as for a limit order, and the map cannot override it.
func TestPlaceStopOrderGate(t *testing.T) {
	cases := []struct {
		name    string
		enabled bool
		req     *quikv1.PlaceStopOrder
	}{
		{"master flag off", false, gzStop(1, nil)},
		{"qty over per-order cap", true, gzStop(2, nil)},
		{"not whitelisted", true, &quikv1.PlaceStopOrder{ClientId: "so:1", Code: "RIU6", Side: quikv1.Side_SIDE_BUY, Quantity: 1}},
		{"no side", true, &quikv1.PlaceStopOrder{ClientId: "so:1", Code: "GZU6", Quantity: 1}},
		{"quantity smuggled", true, gzStop(1, map[string]string{"QUANTITY": "100"})},
		{"account smuggled", true, gzStop(1, map[string]string{"account": "OTHER"})},
		{"action smuggled", true, gzStop(1, map[string]string{"ACTION": "NEW_ORDER"})},
	}
	for _, c := range cases {
		m, b, e := stopMgr(c.enabled)
		if err := m.PlaceStopOrderErr(c.req); err == nil {
			t.Fatalf("%s: must be rejected", c.name)
		}
		if len(b.sent) != 0 {
			t.Fatalf("%s: reached the bridge", c.name)
		}
		if len(e.replies) != 1 || e.replies[0].ResultCode != -1 || e.replies[0].ClientId != c.req.GetClientId() {
			t.Fatalf("%s: reject not reported: %+v", c.name, e.replies)
		}
	}
}

func TestPlaceStopOrderKillSwitchBlocks(t *testing.T) {
	m, b, _ := stopMgr(true)
	m.mu.Lock()
	m.blocked = true
	m.mu.Unlock()
	if err := m.PlaceStopOrderErr(gzStop(1, nil)); err == nil || len(b.sent) != 0 {
		t.Fatalf("kill-switch must block stop placement: err=%v sent=%d", err, len(b.sent))
	}
}

// Removing a stop order reduces exposure: allowed with the master flag off.
func TestKillStopOrderAllowedWithFlagOff(t *testing.T) {
	m, b, _ := stopMgr(false)
	if err := m.KillStopOrderErr(&quikv1.KillStopOrder{ClientId: "so:1", Code: "GZU6",
		StopOrderNum: "1925040213634112099"}); err != nil {
		t.Fatalf("kill: %v", err)
	}
	if len(b.sent) != 1 || b.sent[0].Fields["ACTION"] != "KILL_STOP_ORDER" ||
		b.sent[0].Fields["STOP_ORDER_KEY"] != "1925040213634112099" {
		t.Fatalf("sent %+v", b.sent)
	}
	if err := m.KillStopOrderErr(&quikv1.KillStopOrder{ClientId: "so:1", Code: "GZU6"}); err == nil {
		t.Fatal("kill without a number must be rejected")
	}
}

// QUIK's reply knows only TRANS_ID; STL must still see whose stop order it was.
func TestStopTransReplyCarriesClientID(t *testing.T) {
	m, b, e := stopMgr(true)
	if err := m.PlaceStopOrderErr(gzStop(1, nil)); err != nil {
		t.Fatal(err)
	}
	m.OnTransReply(TransReplyEvent{TransID: b.sent[0].TransID, ResultCode: 3, OrderNum: "555"})
	last := e.replies[len(e.replies)-1]
	if last.ClientId != "so:0123456789" || last.TransId != b.sent[0].TransID {
		t.Fatalf("reply %+v", last)
	}
}

func TestOnStopEventRelaysVerbatim(t *testing.T) {
	m, _, e := stopMgr(true)
	m.OnStopEvent(StopEvent{Fields: map[string]any{
		"order_num": "1925040213634112099", "condition_price": 12340.5, "flags": float64(29), "x": nil}})
	m.OnStopEvent(StopEvent{IsTable: true, Maps: []map[string]any{{"order_num": "1"}, {}}})
	if len(e.reports) != 2 {
		t.Fatalf("reports %d", len(e.reports))
	}
	f := e.reports[0].Rows[0].Fields
	if e.reports[0].IsTable || f["order_num"] != "1925040213634112099" || f["condition_price"] != "12340.5" ||
		f["flags"] != "29" || f["x"] != "" {
		t.Fatalf("event row %v", f)
	}
	if !e.reports[1].IsTable || len(e.reports[1].Rows) != 2 || e.reports[1].Rows[0].Fields["order_num"] != "1" {
		t.Fatalf("table %+v", e.reports[1])
	}
}
