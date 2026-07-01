package trade

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

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
