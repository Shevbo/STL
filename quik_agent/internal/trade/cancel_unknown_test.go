package trade

import (
	"testing"

	quikv1 "shectory/quik_agent/internal/pb"
)

type loudEmit struct {
	alerts  []string
	updates []*quikv1.OrderUpdate
}

func (e *loudEmit) EmitOrderUpdate(u *quikv1.OrderUpdate) error { e.updates = append(e.updates, u); return nil }
func (e *loudEmit) EmitTransReply(*quikv1.TransReply) error     { return nil }
func (e *loudEmit) EmitExecutionUpdate(*quikv1.ExecutionUpdate) error { return nil }
func (e *loudEmit) EmitStopOrderReport(*quikv1.StopOrderReport) error { return nil }
func (e *loudEmit) EmitAlert(sev quikv1.AlertSeverity, code, msg string) error {
	e.alerts = append(e.alerts, code)
	return nil
}

// ОТКАЗ СНЯТИЯ ОБЯЗАН БЫТЬ ГРОМКИМ.
//
// 01.10.2026: после перезапуска агент потерял карту своих заявок, и 24 заявки
// сетки стали неснимаемыми. STL слал отмену, агент МОЛЧА выходил с одной
// строкой в лог, книга писала «снято», а заявки продолжали торговать и набрали
// оператору лишние контракты. Час ушёл только на то, чтобы понять, что отмены
// не доходят вовсе.
func TestCancelOfUnknownOrderIsLoud(t *testing.T) {
	e := &loudEmit{}
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), e, nil)

	m.CancelOrder(&quikv1.CancelOrder{ClientId: "so:grid:gm4:75302", OrderId: "1925040265174971850"})

	if len(e.alerts) == 0 {
		t.Fatal("агент промолчал о невыполненном снятии — именно это стоило часа и контрактов")
	}
	if e.alerts[0] != "CANCEL_UNKNOWN" {
		t.Fatalf("ожидали тревогу CANCEL_UNKNOWN, получили %v", e.alerts)
	}
	if len(e.updates) == 0 {
		t.Fatal("STL обязан получить отказ по заявке, иначе книга напишет «снято»")
	}
}

// СНЯТИЕ ЗАЯВКИ, КОТОРОЙ АГЕНТ НЕ ЗНАЕТ, ОБЯЗАНО РАБОТАТЬ, А НЕ ТОЛЬКО КРИЧАТЬ.
//
// Громкий отказ выше — это признание беспомощности: оператору всё равно
// приходилось идти в терминал и снимать руками. Между тем KILL_ORDER нужны лишь
// номер заявки, класс и инструмент; карта агента для этого не требуется вовсе, и
// CancelOrphan ровно это и делает. Не хватало ОДНОГО ПОЛЯ в CancelOrder:
// инструмента. Теперь STL присылает его из таблицы заявок терминала, и заявка
// снимается независимо от того, помнит ли её агент.
//
// Жалоба оператора 01.10.2026 дословно: «не можешь их снять и пугаешь меня
// амнезией если будет перезагрузка».
func TestCancelOfUnknownOrderWithCodeActuallyCancels(t *testing.T) {
	e := &loudEmit{}
	br := &recBridge{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT"}, br, NewGuard(baseLimits()), e, nil)

	m.CancelOrder(&quikv1.CancelOrder{
		ClientId: "so:grid:gm4:75302",
		OrderId:  "1925040265174971850",
		Code:     "RIZ6",
	})

	if len(br.cancels) != 1 {
		t.Fatalf("снятие не ушло в QUIK: %+v — заявка осталась бы торговать", br.cancels)
	}
	c := br.cancels[0]
	if c.OrderNum != "1925040265174971850" || c.Sec != "RIZ6" || c.Class != "SPBFUT" {
		t.Fatalf("KILL_ORDER собран неверно: %+v", c)
	}
	if len(e.alerts) != 0 {
		t.Fatalf("снятие выполнено — тревоги быть не должно, получили %v", e.alerts)
	}
	if len(e.updates) != 0 {
		t.Fatalf("отказа быть не должно: книга напишет «снято» и будет права, %+v", e.updates)
	}
}

// Без инструмента остаётся прежнее поведение: громкий отказ. Молча глотать
// снятие нельзя — именно молчание стоило часа поиска и лишних контрактов.
func TestCancelOfUnknownOrderWithoutCodeStaysLoud(t *testing.T) {
	e := &loudEmit{}
	br := &recBridge{}
	m := NewManager(ManagerConfig{ClassCode: "SPBFUT"}, br, NewGuard(baseLimits()), e, nil)

	m.CancelOrder(&quikv1.CancelOrder{ClientId: "so:x:1", OrderId: "777"})

	if len(br.cancels) != 0 {
		t.Fatalf("без инструмента KILL_ORDER собрать нечем, ушло: %+v", br.cancels)
	}
	if len(e.alerts) == 0 || e.alerts[0] != "CANCEL_UNKNOWN" {
		t.Fatalf("ожидали CANCEL_UNKNOWN, получили %v", e.alerts)
	}
}
