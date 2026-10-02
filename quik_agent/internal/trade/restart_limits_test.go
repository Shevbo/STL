package trade

import (
	"path/filepath"
	"testing"
)

// ИНВАРИАНТ: перезапуск агента не обнуляет дневной счётчик постановок
// (оператор, 02.10.2026: «рестарт не должен обнулять лимиты»). Свип по числу
// постановок до перезапуска; утверждение — запрет: новый процесс не может
// начать счёт заново.
func TestDailyCounterSurvivesAgentRestart(t *testing.T) {
	for _, before := range []int{1, 7, 499} {
		path := filepath.Join(t.TempDir(), "daily_orders.json")
		lim := Limits{TradingEnabled: true, DailyOrderCap: 500}

		a := NewGuard(lim)
		a.SetCounterStore(path)
		for i := 0; i < before; i++ {
			if ok, why := a.CommitPlace(); !ok {
				t.Fatalf("постановка %d отбита: %s", i+1, why)
			}
		}

		b := NewGuard(lim) // «перезапуск»: новый процесс, тот же файл
		b.SetCounterStore(path)
		if used, _ := b.DailyOrderState(); used != before {
			t.Fatalf("до перезапуска %d постановок, после — %d: счётчик обнулился", before, used)
		}
	}
}

// Счётчик на пределе после перезапуска по-прежнему не пускает заявку.
func TestCapStillHoldsAfterRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "daily_orders.json")
	lim := Limits{TradingEnabled: true, DailyOrderCap: 3}
	a := NewGuard(lim)
	a.SetCounterStore(path)
	for i := 0; i < 3; i++ {
		a.CommitPlace()
	}
	b := NewGuard(lim)
	b.SetCounterStore(path)
	if ok, _ := b.CommitPlace(); ok {
		t.Fatal("после перезапуска кап снова открылся")
	}
}

// Объём в работе после перезапуска: своя карта пуста, в таблице терминала стоят
// наши заявки — объём обязан их видеть, а заявка в пути не задваивается с той же
// строкой таблицы.
func TestWorkingUnitesTerminalAndOwnMap(t *testing.T) {
	m := &Manager{byClient: map[string]*workingOrder{}}
	m.SetRestingSource(func() []RestingRow {
		return []RestingRow{{Num: "n1", Balance: 60}, {Num: "n2", Balance: 40}}
	})
	if got := m.totalWorkingLocked(); got != 100 {
		t.Fatalf("после перезапуска объём %d, ждали 100 по таблице терминала", got)
	}

	m.byClient["c1"] = &workingOrder{clientID: "c1", orderNum: "n2", qty: 40}
	m.byClient["c2"] = &workingOrder{clientID: "c2", qty: 5} // в пути, номера нет
	if got := m.totalWorkingLocked(); got != 105 {
		t.Fatalf("объём %d, ждали 105: n2 из карты не должна задвоиться со строкой таблицы", got)
	}

	m.SetRestingSource(func() []RestingRow { return nil }) // таблица неизвестна
	if got := m.totalWorkingLocked(); got != 45 {
		t.Fatalf("без таблицы объём %d, ждали 45 по своей карте", got)
	}
}
