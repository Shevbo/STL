package trade

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

// ---- SnapshotWorking ----

func TestSnapshotWorkingSkipsDoneAndSortsByOrderNum(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)

	// Two live working orders, out of OrderNum order on purpose.
	m.byClient["b"] = &workingOrder{
		clientID: "b", orderNum: "200", code: "RIU6",
		price: 100005, qty: 2, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_PARTIAL,
	}
	m.byClient["a"] = &workingOrder{
		clientID: "a", orderNum: "100", code: "GZU6",
		price: 55000, qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_ACTIVE,
	}
	// A terminal (done) order must NOT appear in the snapshot.
	m.byClient["done"] = &workingOrder{
		clientID: "done", orderNum: "300", code: "RIU6",
		price: 100010, qty: 1, balance: 0, done: true,
		state: quikv1.OrderState_ORDER_STATE_FILLED,
	}
	// A PENDING order with no order_num yet (nothing assigned by QUIK) must still
	// appear (it's live, just unkeyed) — OrderNum renders empty.
	m.byClient["pending"] = &workingOrder{
		clientID: "pending", code: "SiU6",
		price: 90000, qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_PENDING,
	}

	got := m.SnapshotWorking()
	if len(got) != 3 {
		t.Fatalf("snapshot = %+v, want 3 live entries (done excluded)", got)
	}
	// Sorted by OrderNum ascending; the empty-OrderNum pending entry sorts first.
	if got[0].ClientID != "pending" || got[0].OrderNum != "" {
		t.Fatalf("got[0] = %+v, want pending first (empty order_num)", got[0])
	}
	if got[1].OrderNum != "100" || got[1].ClientID != "a" || got[1].Code != "GZU6" {
		t.Fatalf("got[1] = %+v, want order_num 100 (client a)", got[1])
	}
	if got[2].OrderNum != "200" || got[2].ClientID != "b" || got[2].Price != 100005 ||
		got[2].Qty != 2 || got[2].Balance != 1 {
		t.Fatalf("got[2] = %+v, want order_num 200 (client b) fields intact", got[2])
	}
	for _, w := range got {
		if w.OrderNum == "300" {
			t.Fatalf("done order 300 leaked into snapshot: %+v", got)
		}
	}
}

func TestSnapshotWorkingEmpty(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	got := m.SnapshotWorking()
	if len(got) != 0 {
		t.Fatalf("snapshot = %+v, want empty", got)
	}
}

// ---- PendingTransViews ----

func TestPendingTransViewsHungPastReconcileWindow(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	now := int64(10_000_000)
	m.nowMsFn = func() int64 { return now }

	// Hung: still PENDING, no order_num, sent well past the reconcile window.
	m.byTrans[1] = &workingOrder{
		clientID: "hung", transID: 1, code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_PENDING,
		qty:   1, balance: 1, sentMs: now - (staleAckTimeoutMs + 5_000),
	}
	// Fresh pending: NOT past the window — must not appear.
	m.byTrans[2] = &workingOrder{
		clientID: "fresh", transID: 2, code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_PENDING,
		qty:   1, balance: 1, sentMs: now - 1_000,
	}
	// Active order (registered, has order_num) — must not appear even if old.
	m.byTrans[3] = &workingOrder{
		clientID: "active", transID: 3, orderNum: "555", code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_ACTIVE,
		qty:   1, balance: 1, sentMs: now - (staleAckTimeoutMs + 5_000),
	}
	// Rejected recently: must appear (within the rejected surface window).
	m.byTrans[4] = &workingOrder{
		clientID: "rejected", transID: 4, code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_REJECTED, done: true,
		lastText: "collar breached", sentMs: now - 500, rejectedMs: now - 500,
	}

	got := m.PendingTransViews()
	if len(got) != 2 {
		t.Fatalf("views = %+v, want exactly 2 (hung + rejected)", got)
	}
	// Sorted by TransID ascending.
	if got[0].TransID != 1 || got[0].OK {
		t.Fatalf("got[0] = %+v, want trans 1 (hung), OK=false", got[0])
	}
	if got[1].TransID != 4 || got[1].OK || got[1].Text != "collar breached" {
		t.Fatalf("got[1] = %+v, want trans 4 (rejected) with its last text, OK=false", got[1])
	}
}

// TestPendingTransViewsRejectedAgesOut: a rejection older than the surface window is
// excluded (byTrans is never pruned — without the recency bound one old rejection would
// pin the recon State to MISMATCH until process restart); a fresh one is included.
func TestPendingTransViewsRejectedAgesOut(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	now := int64(100_000_000)
	m.nowMsFn = func() int64 { return now }

	// Old rejection: outside the 15-minute window — must NOT appear.
	m.byTrans[1] = &workingOrder{
		clientID: "old", transID: 1, code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_REJECTED, done: true,
		lastText: "stale reject", rejectedMs: now - (rejectedSurfaceWindowMs + 1),
	}
	// Fresh rejection: just inside the window — must appear.
	m.byTrans[2] = &workingOrder{
		clientID: "fresh", transID: 2, code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_REJECTED, done: true,
		lastText: "fresh reject", rejectedMs: now - (rejectedSurfaceWindowMs - 1),
	}

	got := m.PendingTransViews()
	if len(got) != 1 || got[0].TransID != 2 || got[0].Text != "fresh reject" || got[0].OK {
		t.Fatalf("views = %+v, want only the fresh rejection (trans 2)", got)
	}

	// The fresh one ages out too once the window passes.
	now += rejectedSurfaceWindowMs
	if got := m.PendingTransViews(); len(got) != 0 {
		t.Fatalf("views after window elapsed = %+v, want none", got)
	}
}

// TestSnapshotWorkingTiebreaksByClientID: two live PENDING orders both have an empty
// OrderNum until QUIK acknowledges them; ClientID must keep the order deterministic.
func TestSnapshotWorkingTiebreaksByClientID(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	m.byClient["p2"] = &workingOrder{
		clientID: "p2", code: "RIU6", price: 100000, qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_PENDING,
	}
	m.byClient["p1"] = &workingOrder{
		clientID: "p1", code: "RIU6", price: 100000, qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_PENDING,
	}
	got := m.SnapshotWorking()
	if len(got) != 2 || got[0].ClientID != "p1" || got[1].ClientID != "p2" {
		t.Fatalf("snapshot = %+v, want [p1 p2] (ClientID tiebreak on empty OrderNum)", got)
	}
}

func TestPendingTransViewsNoneWhenClean(t *testing.T) {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	now := int64(10_000_000)
	m.nowMsFn = func() int64 { return now }
	m.byTrans[1] = &workingOrder{
		clientID: "active", transID: 1, orderNum: "555", code: "RIU6",
		state: quikv1.OrderState_ORDER_STATE_ACTIVE,
		qty:   1, balance: 1, sentMs: now - 1_000,
	}
	got := m.PendingTransViews()
	if len(got) != 0 {
		t.Fatalf("views = %+v, want none", got)
	}
}

// TestPendingTransViewsLastTextTracksOnTransReply drives the real event path (OnTransReply)
// rather than poking workingOrder fields directly, proving lastText is captured where the
// manager actually learns it.
func TestPendingTransViewsLastTextTracksOnTransReply(t *testing.T) {
	fe := &fakeEmit{}
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), fe, nil)
	now := int64(1_000)
	m.nowMsFn = func() int64 { return now }

	wo := &workingOrder{clientID: "c1", transID: 7, code: "RIU6", state: quikv1.OrderState_ORDER_STATE_PENDING, qty: 1, balance: 1, sentMs: now}
	m.byClient["c1"] = wo
	m.byTrans[7] = wo

	m.OnTransReply(TransReplyEvent{TransID: 7, ResultCode: 5, Text: "не хватает лимита"})

	got := m.PendingTransViews()
	if len(got) != 1 || got[0].TransID != 7 || got[0].Text != "не хватает лимита" || got[0].OK {
		t.Fatalf("views = %+v, want trans 7 rejected with its reply text", got)
	}
}
