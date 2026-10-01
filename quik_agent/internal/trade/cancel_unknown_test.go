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
