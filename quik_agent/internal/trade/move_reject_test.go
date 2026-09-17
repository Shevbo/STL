package trade

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

// Отказ перестановки не убивает заявку: она осталась стоять в QUIK по прежней цене.
// 17.09.2026 отказ MOVE_ORDERS по GZZ6 пометил живую заявку REJECTED+done, и STL
// считал мёртвой заявку, которая торговала.
func TestMoveRejectKeepsOrderWorking(t *testing.T) {
	br := &recBridge{}
	em := &fakeEmit{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT", Account: "A1"}, br,
		NewGuard(baseLimits()), em, nil)

	wo := &workingOrder{
		clientID: "so:abc", transID: 10, orderNum: "555", code: "GZZ6",
		side: quikv1.Side_SIDE_BUY, price: 9574, qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_ACTIVE,
	}
	m.byClient[wo.clientID] = wo
	m.byTrans[10] = wo
	m.byOrder["555"] = wo

	m.sendMove(wo, 9590, 0)
	moveTrans := br.trans
	if wo.moveTransID != moveTrans || wo.price != 9590 {
		t.Fatalf("после отправки перестановки: moveTrans=%d price=%v", wo.moveTransID, wo.price)
	}

	// QUIK отказал: «Неверные параметры транзакции» (result_code 4).
	m.OnTransReply(TransReplyEvent{TransID: moveTrans, ResultCode: 4, Text: "Неверные параметры транзакции"})

	if wo.done || wo.state == quikv1.OrderState_ORDER_STATE_REJECTED {
		t.Fatalf("живая заявка помечена мёртвой: state=%v done=%v", wo.state, wo.done)
	}
	if wo.price != 9574 || wo.qty != 1 || wo.transID != 10 {
		t.Fatalf("оптимистичное состояние не откатано: price=%v qty=%d trans=%d", wo.price, wo.qty, wo.transID)
	}
	if m.superseded["555"] {
		t.Fatalf("метка superseded осталась: снятие старой заявки будет проглочено")
	}
	if len(em.orders) != 1 || em.orders[0].GetPrice() != 9574 {
		t.Fatalf("STL не получил обновление с прежней ценой: %+v", em.orders)
	}
}

// Отказ САМОЙ постановки (а не перестановки) заявку по-прежнему хоронит.
func TestPlacementRejectStillKillsOrder(t *testing.T) {
	em := &fakeEmit{}
	m := NewManager(ManagerConfig{}, &recBridge{}, NewGuard(baseLimits()), em, nil)
	wo := &workingOrder{
		clientID: "so:def", transID: 7, code: "GZZ6", qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_PENDING,
	}
	m.byClient[wo.clientID] = wo
	m.byTrans[7] = wo

	m.OnTransReply(TransReplyEvent{TransID: 7, ResultCode: 4, Text: "Отказ"})

	if !wo.done || wo.state != quikv1.OrderState_ORDER_STATE_REJECTED {
		t.Fatalf("отказ постановки должен хоронить заявку: state=%v done=%v", wo.state, wo.done)
	}
}

// Принятая перестановка снимает учёт: следующий отказ по другой транзакции не
// должен считаться отказом перестановки.
func TestAcceptedMoveClearsBookkeeping(t *testing.T) {
	br := &recBridge{}
	m := NewManager(ManagerConfig{}, br, NewGuard(baseLimits()), &fakeEmit{}, nil)
	wo := &workingOrder{
		clientID: "so:ghi", transID: 3, orderNum: "777", code: "GZZ6",
		side: quikv1.Side_SIDE_SELL, price: 9600, qty: 1, balance: 1,
		state: quikv1.OrderState_ORDER_STATE_ACTIVE,
	}
	m.byClient[wo.clientID] = wo
	m.byTrans[3] = wo
	m.byOrder["777"] = wo

	m.sendMove(wo, 9595, 0)
	m.OnTransReply(TransReplyEvent{TransID: br.trans, ResultCode: 3, Text: "Заявка зарегистрирована"})
	if wo.moveTransID != 0 || wo.price != 9595 {
		t.Fatalf("принятая перестановка: moveTrans=%d price=%v", wo.moveTransID, wo.price)
	}
}
