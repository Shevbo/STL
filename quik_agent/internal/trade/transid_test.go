package trade

import (
	"path/filepath"
	"testing"
	"time"
)

// ИНВАРИАНТ: номер транзакции НИКОГДА не повторяется в пределах сеанса QUIK, как
// бы часто ни перезапускался агент. Терминал живёт дольше агента и повторный
// TRANS_ID игнорирует МОЛЧА — ни заявки, ни отказа. 02.10.2026 перезапуск агента
// посреди торгов обнулил счётчик, и каждая заявка умирала по таймауту «не
// зарегистрирована в QUIK (нет ответа)».
//
// Свип по числу выданных номеров ДО перезапуска, включая переход через границу
// блока резервирования: утверждение — ЗАПРЕТ (новый номер не может оказаться
// меньше или равен старому), а не пример с конкретным числом.
func TestTransIDNeverRepeatsAcrossRestarts(t *testing.T) {
	for _, issued := range []int{1, 2, 17, 1000, transIDBlock - 1, transIDBlock, transIDBlock + 1, 3*transIDBlock + 7} {
		path := filepath.Join(t.TempDir(), "trans_id.txt")

		first := NewBridge(0, nil, nil)
		first.SetTransIDStore(path)
		var last int64
		for i := 0; i < issued; i++ {
			v := first.NextTransID()
			if v <= last {
				t.Fatalf("выдано %d: номера не растут внутри одного запуска: %d после %d", issued, v, last)
			}
			last = v
		}

		// Перезапуск: новый процесс, тот же файл.
		second := NewBridge(0, nil, nil)
		second.SetTransIDStore(path)
		next := second.NextTransID()
		if next <= last {
			t.Fatalf("выдано %d: после перезапуска номер %d <= прежнего %d — QUIK молча проглотит такую заявку",
				issued, next, last)
		}
	}
}

// Файл потерян (переезд, чистка каталога, первый запуск) — гарантия слабее, и
// честно сказать, в чём именно она состоит: номер НЕ возвращается в низкий
// диапазон, который терминал уже израсходовал. Строгого «выше прошлого» тут
// обещать нельзя: два старта в одну и ту же секунду дадут одно основание. Это
// приемлемо потому, что как только агент выдал хоть один номер, файл уже есть, и
// работает гарантия из теста выше.
func TestTransIDWithoutStoreStartsAboveConsumedRange(t *testing.T) {
	before := time.Now().Unix()
	b := NewBridge(0, nil, nil)
	b.SetTransIDStore(filepath.Join(t.TempDir(), "trans_id.txt")) // файла нет
	if v := b.NextTransID(); v <= before {
		t.Fatalf("без файла номер %d не выше unix-секунд %d — такой уже мог быть выдан сегодня", v, before)
	}
}

// Без файла (тесты, файловый транспорт) поведение прежнее: с нуля и по возрастанию.
func TestTransIDWithoutStoreStaysSequential(t *testing.T) {
	b := NewBridge(0, nil, nil)
	if v := b.NextTransID(); v != 1 {
		t.Fatalf("без хранилища первый номер %d, ожидали 1", v)
	}
	if v := b.NextTransID(); v != 2 {
		t.Fatalf("без хранилища второй номер %d, ожидали 2", v)
	}
}
