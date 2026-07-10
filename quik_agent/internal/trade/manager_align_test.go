package trade

// Task 8: the recon Aligner's Manager surface. PlaceOrderErr is PlaceOrder
// returning the rejection (the full Guard/master-flag/kill-switch path is
// UNCHANGED — same rejects, same emitted OrderUpdates); CancelOrphan issues a
// KILL_ORDER for an order the Manager does not track (a recon ORPHAN).

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

type recBridge struct {
	trans     int64
	places    []placeCmd
	cancels   []cancelCmd
	placeErr  error
	cancelErr error
}

func (b *recBridge) NextTransID() int64 { b.trans++; return b.trans }
func (b *recBridge) Place(p placeCmd) error {
	b.places = append(b.places, p)
	return b.placeErr
}
func (b *recBridge) Cancel(c cancelCmd) error {
	b.cancels = append(b.cancels, c)
	return b.cancelErr
}
func (b *recBridge) Move(moveCmd) error { return nil }
func (b *recBridge) Connected() bool    { return true }

func TestPlaceOrderErr_AcceptedReturnsNil(t *testing.T) {
	br := &recBridge{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT", Account: "A1"}, br,
		NewGuard(baseLimits()), &fakeEmit{}, nil)
	err := m.PlaceOrderErr(&quikv1.PlaceOrder{
		ClientId: "recon:abc", Code: "RIU6", Side: quikv1.Side_SIDE_SELL,
		Price: 89170, Quantity: 1})
	if err != nil {
		t.Fatalf("accepted placement must return nil, got %v", err)
	}
	if len(br.places) != 1 || br.places[0].Sec != "RIU6" || br.places[0].Op != "S" {
		t.Fatalf("bridge place = %+v", br.places)
	}
}

func TestPlaceOrderErr_MasterFlagOffReturnsRejection(t *testing.T) {
	lim := baseLimits()
	lim.TradingEnabled = false // disarmed agent
	br := &recBridge{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT"}, br, NewGuard(lim), &fakeEmit{}, nil)
	err := m.PlaceOrderErr(&quikv1.PlaceOrder{
		ClientId: "recon:abc", Code: "RIU6", Side: quikv1.Side_SIDE_SELL,
		Price: 89170, Quantity: 1})
	if err == nil || err.Error() != string(ReasonTradingDisabled) {
		t.Fatalf("want %q, got %v", ReasonTradingDisabled, err)
	}
	if len(br.places) != 0 {
		t.Fatalf("a rejected order must never reach the bridge: %+v", br.places)
	}
}

func TestPlaceOrderErr_NotWhitelistedReturnsRejection(t *testing.T) {
	br := &recBridge{}
	m := NewManager(ManagerConfig{}, br, NewGuard(baseLimits()), &fakeEmit{}, nil)
	err := m.PlaceOrderErr(&quikv1.PlaceOrder{
		ClientId: "recon:abc", Code: "GZU6", Side: quikv1.Side_SIDE_BUY,
		Price: 100, Quantity: 1})
	if err == nil || err.Error() != string(ReasonNotWhitelisted) {
		t.Fatalf("want %q, got %v", ReasonNotWhitelisted, err)
	}
}

func TestCancelOrphan_SendsKillWithClassAndSec(t *testing.T) {
	br := &recBridge{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT"}, br, NewGuard(baseLimits()),
		&fakeEmit{}, nil)
	if err := m.CancelOrphan("987654", "RIU6"); err != nil {
		t.Fatalf("CancelOrphan: %v", err)
	}
	if len(br.cancels) != 1 {
		t.Fatalf("want one cancel, got %+v", br.cancels)
	}
	c := br.cancels[0]
	if c.OrderNum != "987654" || c.Class != "SPBFUT" || c.Sec != "RIU6" {
		t.Fatalf("cancel cmd = %+v", c)
	}
}

func TestCancelOrphan_EmptyOrderNumFails(t *testing.T) {
	m := NewManager(ManagerConfig{}, &recBridge{}, NewGuard(baseLimits()), &fakeEmit{}, nil)
	if err := m.CancelOrphan("", "RIU6"); err == nil {
		t.Fatal("empty order_num must fail, not send a broken KILL_ORDER")
	}
}

// A marketable order that fills BETTER than its limit price (price improvement)
// must surface the REAL execution price in the OrderUpdate the runner books —
// not the order price. Live 10.07: buy placed @87330 filled @87310; the runner
// booked 87330 and recon flagged a false MISMATCH.
func TestOnTradePriceImprovementFlowsToOrderUpdate(t *testing.T) {
	br := &recBridge{}
	em := &fakeEmit{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT", Account: "A1"}, br,
		NewGuard(baseLimits()), em, nil)
	if err := m.PlaceOrderErr(&quikv1.PlaceOrder{
		ClientId: "rr:r1:1:abcdef", Code: "RIU6",
		Side: quikv1.Side_SIDE_BUY, Price: 87330, Quantity: 1}); err != nil {
		t.Fatal(err)
	}
	transID := br.trans
	m.OnTransReply(TransReplyEvent{TransID: transID, ResultCode: 3, OrderNum: "555"})
	m.OnTrade(TradeEvent{OrderNum: "555", Qty: 1, Price: "87310"}) // improved
	m.OnOrder(OrderEvent{OrderNum: "555", TransID: transID, State: "filled",
		Balance: 0, Qty: 1, Price: "87330"})

	last := em.orders[len(em.orders)-1]
	if last.GetState() != quikv1.OrderState_ORDER_STATE_FILLED {
		t.Fatalf("last update state = %v, want FILLED", last.GetState())
	}
	if last.GetPrice() != 87310 {
		t.Fatalf("filled update price = %v, want trade price 87310 (not order price 87330)", last.GetPrice())
	}
}

// OnOrder arriving BEFORE any OnTrade (race) still emits the order price —
// the best information available at that moment.
func TestOnOrderWithoutTradeKeepsOrderPrice(t *testing.T) {
	br := &recBridge{}
	em := &fakeEmit{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT", Account: "A1"}, br,
		NewGuard(baseLimits()), em, nil)
	if err := m.PlaceOrderErr(&quikv1.PlaceOrder{
		ClientId: "rr:r1:2:abcdef", Code: "RIU6",
		Side: quikv1.Side_SIDE_BUY, Price: 87330, Quantity: 1}); err != nil {
		t.Fatal(err)
	}
	m.OnOrder(OrderEvent{OrderNum: "556", TransID: br.trans, State: "filled",
		Balance: 0, Qty: 1, Price: "87330"})
	last := em.orders[len(em.orders)-1]
	if last.GetPrice() != 87330 {
		t.Fatalf("price = %v, want order price 87330", last.GetPrice())
	}
}
