package trade

import (
	"fmt"
	"testing"
)

func newExpAt(t0 *int64) *ExposureGuard {
	return NewExposureGuard(func() int64 { return *t0 })
}

// ГЛАВНЫЙ УРОК 30.09.2026, ценой 43 контрактов оператора: сорок три продажи по
// одному лоту за три минуты, каждая по отдельности законная.
func TestRunawaySourceIsStoppedInItsOwnDirection(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	cid := "so:abc123:top"

	for i := 0; i < expSourceCap; i++ {
		if stop, why := g.Check(cid, "RIZ6", false); stop {
			t.Fatalf("продажа %d остановлена слишком рано: %s", i+1, why)
		}
		// client_id уникален на КАЖДУЮ попытку — так и было у сторожа 30.09.
		g.Observe(fmt.Sprintf("%s:%d", cid, i), "RIZ6", false, 1)
		now += 4_000 // сторож ставил примерно раз в десять секунд
	}
	stop, why := g.Check(cid, "RIZ6", false)
	if !stop {
		t.Fatalf("после %d проданных контрактов продажа обязана быть остановлена", expSourceCap)
	}
	if why == "" {
		t.Fatal("причина обязана быть человеческой: она уходит в тревогу оператору")
	}
}

// Дверь выхода не запирается никогда: 21.07.2026 исчерпанный дневной кап
// заморозил роботам ВЫХОДЫ на два с половиной часа.
func TestReverseOrderIsNeverBlocked(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	cid := "robot:agent-macdshort-RIU6-v1"

	for i := 0; i < expSourceCap*3; i++ {
		g.Observe(fmt.Sprintf("%s:%d", cid, i), "RIZ6", false, 1)
		now += 1_000
	}
	if stop, _ := g.Check(cid, "RIZ6", false); !stop {
		t.Fatal("продажа обязана быть остановлена")
	}
	if stop, why := g.Check(cid, "RIZ6", true); stop {
		t.Fatalf("ПОКУПКА (выход из шорта) заблокирована — этого быть не может: %s", why)
	}
}

// Чужая беда не затыкает соседа: замолкает источник, а не весь агент.
func TestOtherSourceKeepsTrading(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	for i := 0; i < expSourceCap; i++ {
		g.Observe(fmt.Sprintf("so:runaway:top:%d", i), "RIZ6", false, 1)
	}
	if stop, _ := g.Check("so:runaway:top:x", "RIZ6", false); !stop {
		t.Fatal("разогнавшийся источник обязан молчать")
	}
	if stop, why := g.Check("rr:quiet-one:x", "RIZ6", false); stop {
		t.Fatalf("сосед остановлен за чужой разгон: %s", why)
	}
}

// Счёт целиком: несколько источников вместе тоже разгоняют позицию.
func TestAccountCapCatchesSeveralSourcesTogether(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	for i := 0; i < expAccountCap/2; i++ {
		g.Observe(fmt.Sprintf("rr:robot-a:%d", i), "RIZ6", false, 1)
		g.Observe(fmt.Sprintf("rr:robot-b:%d", i), "RIZ6", false, 1)
	}
	// ни один источник поодиночке не дошёл до своего порога? дошёл — возьмём третий,
	// который не торговал вовсе: его останавливает именно счётный порог
	if stop, why := g.Check("rr:robot-c:x", "RIZ6", false); !stop {
		t.Fatal("счётный порог не сработал")
	} else if why == "" {
		t.Fatal("причина пуста")
	}
	if stop, _ := g.Check("rr:robot-c:x", "RIZ6", true); stop {
		t.Fatal("покупка против разгона обязана проходить")
	}
}

// Окно скользит: вчерашние филлы не держат источник в блокировке вечно.
func TestOldFillsLeaveTheWindow(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	for i := 0; i < expSourceCap; i++ {
		g.Observe(fmt.Sprintf("rr:robot-a:%d", i), "RIZ6", false, 1)
	}
	now += expWindowMs + expCooldownMs + 1
	if stop, why := g.Check("rr:robot-a:x", "RIZ6", false); stop {
		t.Fatalf("после окна и остывания источник обязан ожить: %s", why)
	}
}

// Разные инструменты не смешиваются: −20 в RIZ6 не запрещают продавать SiZ6.
func TestInstrumentsAreCountedApart(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	for i := 0; i < expSourceCap*2; i++ {
		g.Observe(fmt.Sprintf("rr:robot-a:%d", i), "RIZ6", false, 1)
	}
	if stop, why := g.Check("rr:robot-a:x", "SiZ6", false); stop {
		t.Fatalf("чужой инструмент попал под чужой разгон: %s", why)
	}
}

// Встречные филлы гасят друг друга: пила вокруг нуля не есть разгон.
func TestOppositeFillsCancelOut(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	for i := 0; i < expSourceCap*3; i++ {
		g.Observe(fmt.Sprintf("rr:robot-a:s%d", i), "RIZ6", false, 1)
		g.Observe(fmt.Sprintf("rr:robot-a:b%d", i), "RIZ6", true, 1)
		now += 1_000
	}
	if stop, why := g.Check("rr:robot-a:x", "RIZ6", false); stop {
		t.Fatalf("чистая позиция не сдвинулась, останавливать не за что: %s", why)
	}
}


// ПЕРЕВОРОТ НЕ ЕСТЬ РАЗГОН. У lxk22 max_position=24: переворот шорта в лонг идёт
// ДВУМЯ заявками подряд (выход всей позицией, следом вход в новую сторону), то
// есть чистый сдвиг +48 за секунды. Без условия на число заявок вход был бы
// отклонён, и робот остался бы в флэте вместо лонга — ровно тот отказ, что уже
// стоил денег на кросс-заявках.
func TestReversalOfAFullPositionIsNotARunaway(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	const rid = "rr:lxk22tsffsxiiotb8kmpsato"

	// выход из шорта −24 одной заявкой; наливается частями, но заявка ОДНА
	if stop, why := g.Check(rid+":1", "RIZ6", true); stop {
		t.Fatalf("выход заблокирован: %s", why)
	}
	for i := 0; i < 24; i++ {
		g.Observe(rid+":1", "RIZ6", true, 1)
		now += 50
	}
	// вход в лонг на 24 — вторая заявка, сдвиг станет +48
	if stop, why := g.Check(rid+":2", "RIZ6", true); stop {
		t.Fatalf("ВХОД ПЕРЕВОРОТА отклонён как разгон: %s", why)
	}
	for i := 0; i < 24; i++ {
		g.Observe(rid+":2", "RIZ6", true, 1)
		now += 50
	}
	// третья заявка в ту же сторону — это уже не переворот, но заявок всё ещё
	// меньше порога: объём один не делает разгон
	if stop, _ := g.Check(rid+":3", "RIZ6", true); stop {
		t.Fatal("две заявки не разгон, сколько бы контрактов в них ни было")
	}
}

// А вот ПЯТЬ заявок с тем же объёмом — разгон: повторяемость и есть признак.
func TestManySmallOrdersAreARunawayAtTheSameVolume(t *testing.T) {
	now := int64(1_790_800_000_000)
	g := newExpAt(&now)
	const rid = "rr:lxk22tsffsxiiotb8kmpsato"
	for i := 0; i < expMinOrders; i++ {
		g.Observe(fmt.Sprintf("%s:%d", rid, i), "RIZ6", true, expSourceCap/expMinOrders+1)
		now += 2_000
	}
	if stop, _ := g.Check(rid+":next", "RIZ6", true); !stop {
		t.Fatal("пять заявок на тот же объём обязаны считаться разгоном")
	}
}
