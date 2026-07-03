package trade

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

// fakeEmit records emitted frames for the sweep test.
type fakeEmit struct {
	orders []*quikv1.OrderUpdate
	alerts []string
}

func (f *fakeEmit) EmitOrderUpdate(u *quikv1.OrderUpdate) error {
	f.orders = append(f.orders, u)
	return nil
}
func (f *fakeEmit) EmitTransReply(*quikv1.TransReply) error         { return nil }
func (f *fakeEmit) EmitExecutionUpdate(*quikv1.ExecutionUpdate) error { return nil }
func (f *fakeEmit) EmitAlert(_ quikv1.AlertSeverity, code, _ string) error {
	f.alerts = append(f.alerts, code)
	return nil
}

func TestReconcileStalePending(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	now := int64(10_000_000)
	m.nowMsFn = func() int64 { return now }

	// fresh pending (just sent, no order_num yet) — must NOT expire.
	m.byClient["fresh"] = &workingOrder{
		clientID: "fresh", state: quikv1.OrderState_ORDER_STATE_PENDING,
		qty: 1, balance: 1, sentMs: now - 1_000,
	}
	// phantom pending: no order_num, older than the ack timeout — MUST expire.
	m.byClient["phantom"] = &workingOrder{
		clientID: "phantom", code: "RIU6", state: quikv1.OrderState_ORDER_STATE_PENDING,
		qty: 1, balance: 1, sentMs: now - (staleAckTimeoutMs + 5_000),
	}
	// registered active order (has order_num) — must NOT expire even when old.
	m.byClient["active"] = &workingOrder{
		clientID: "active", orderNum: "123", state: quikv1.OrderState_ORDER_STATE_ACTIVE,
		qty: 1, balance: 1, sentMs: now - (staleAckTimeoutMs + 5_000),
	}

	m.mu.Lock()
	before := m.totalWorkingLocked()
	m.mu.Unlock()
	if before != 3 {
		t.Fatalf("working before = %d, want 3", before)
	}

	stale := m.reconcileStalePending()
	if len(stale) != 1 || stale[0].clientID != "phantom" {
		t.Fatalf("stale = %+v, want exactly [phantom]", stale)
	}
	if !m.byClient["phantom"].done ||
		m.byClient["phantom"].state != quikv1.OrderState_ORDER_STATE_REJECTED {
		t.Fatal("phantom must be terminal (done + rejected)")
	}
	if m.byClient["fresh"].done || m.byClient["active"].done {
		t.Fatal("fresh/active orders must be untouched")
	}

	m.mu.Lock()
	after := m.totalWorkingLocked()
	m.mu.Unlock()
	if after != 2 {
		t.Fatalf("working after = %d, want 2 (phantom freed)", after)
	}
}

func TestSweepStaleRaisesAlertOncePerIncident(t *testing.T) {
	fe := &fakeEmit{}
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), fe, nil)
	now := int64(10_000_000)
	m.nowMsFn = func() int64 { return now }
	m.byClient["phantom"] = &workingOrder{
		clientID: "phantom", code: "SRU6", state: quikv1.OrderState_ORDER_STATE_PENDING,
		qty: 1, balance: 1, sentMs: now - (staleAckTimeoutMs + 5_000),
	}

	m.SweepStale()
	if len(fe.alerts) != 1 || fe.alerts[0] != "QUIK_NO_REPLY" {
		t.Fatalf("alerts = %v, want exactly [QUIK_NO_REPLY]", fe.alerts)
	}
	if len(fe.orders) != 1 {
		t.Fatalf("order updates = %d, want 1 (the expired phantom)", len(fe.orders))
	}
	// A second sweep must NOT re-alert: the phantom is already terminal.
	m.SweepStale()
	if len(fe.alerts) != 1 {
		t.Fatalf("alert must fire once per incident, got %d", len(fe.alerts))
	}
}
