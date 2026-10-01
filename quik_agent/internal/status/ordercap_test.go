package status

import (
	"fmt"
	"testing"

	"shectory/quik_agent/internal/accounts"
)

// ЖИВЫЕ ЗАЯВКИ НЕ РЕЖУТСЯ НИКОГДА.
//
// Хвост в 100 строк брался без разбора активности. Таблица заявок QUIK это весь
// день целиком, и 01.10.2026 она отдавала РОВНО 100 строк — кап уже был достигнут,
// а 25 живых заявок сетки держались в выдаче случайно, оказавшись среди новейших.
// На оборотистом дне утренние заявки уехали бы за кап, и STL поставил бы второй
// уровень поверх живого, а снять первый было бы нечем: его нет ни в одной таблице,
// которую STL видит.
//
// Сделка, не попавшая в хвост, портит учёт. Заявка, не попавшая в хвост, ТОРГУЕТ.
func TestLiveOrdersAreNeverTruncated(t *testing.T) {
	st := accounts.New(func() int64 { return 1790868800000 })
	rows := make([]accounts.Order, 0, 400)
	// ПОРЯДОК ЗДЕСЬ И ЕСТЬ ВСЯ СУТЬ. QUIK отдаёт таблицу хронологически, и хвост в
	// 100 строк — это НОВЕЙШИЕ. Сетку оператор ставит утром, оборот идёт весь день,
	// значит живые заявки лежат РАНЬШЕ истории и уезжают за кап первыми.
	//
	// Поставь их в конце — и старый код тоже пройдёт тест, потому что 25 живых
	// попадут в хвост сами. Именно так я написал этот тест с первого раза, и он
	// зеленел на дефектном капе, то есть не доказывал ничего.
	for i := 0; i < 25; i++ {
		rows = append(rows, accounts.Order{Num: fmt.Sprintf("live-%d", i), Sec: "RIZ6",
			Side: "B", Price: float64(84540 + i*100), Qty: 1, Balance: 1,
			Active: true, Tag: "stl-so-41bf3af0dd:gm"})
	}
	for i := 0; i < 300; i++ {
		rows = append(rows, accounts.Order{Num: fmt.Sprintf("dead-%d", i), Sec: "RIZ6",
			Side: "B", Price: 85000, Qty: 1, Balance: 0, Active: false, Tag: ""})
	}
	st.SetOrders(rows)

	q := buildQuikJSON(st.Snapshot())
	live := 0
	for _, o := range q.Orders {
		if o.Active {
			live++
		}
	}
	if live != 25 {
		t.Fatalf("живых заявок в снимке %d из 25 — срезанная заявка продолжит торговать, а STL её не увидит", live)
	}
	if len(q.Orders) > 25+quikOrdersCap {
		t.Fatalf("история не обрезана: %d строк при капе %d", len(q.Orders), quikOrdersCap)
	}
}

// История всё же режется — иначе снимок растёт без границ на оборотистом дне.
func TestInactiveHistoryIsStillCapped(t *testing.T) {
	st := accounts.New(func() int64 { return 1790868800000 })
	rows := make([]accounts.Order, 0, 500)
	for i := 0; i < 500; i++ {
		rows = append(rows, accounts.Order{Num: fmt.Sprintf("dead-%d", i), Sec: "RIZ6",
			Side: "S", Price: 85000, Qty: 1, Balance: 0, Active: false})
	}
	st.SetOrders(rows)
	q := buildQuikJSON(st.Snapshot())
	if len(q.Orders) != quikOrdersCap {
		t.Fatalf("неактивных строк %d, ожидали кап %d", len(q.Orders), quikOrdersCap)
	}
	// Newest first: хвост истории, а не её начало.
	if q.Orders[0].Num != "dead-499" {
		t.Fatalf("порядок сломан: первая строка %q, ожидали новейшую dead-499", q.Orders[0].Num)
	}
}
