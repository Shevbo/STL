package trade

import "testing"

// 30.09.2026: робот получил от брокера 26 одинаковых отказов подряд — «Нехватка
// средств по лимитам клиента» — по одному в минуту, полчаса. Каждая попытка по
// отдельности законна, поэтому её никто не считал. Оператор потребовал обрывать
// такие циклы: брокер берёт деньги за транзакции сверх лимита частоты, а серия
// одинаковых отказов означает, что источник живёт в неверной картине мира.
func TestLoopGuardStopsARepeatingRejection(t *testing.T) {
	now := int64(1_000_000)
	g := NewLoopGuard(func() int64 { return now })
	cid := "rr:agent-macdshort-RIU6-v1:8:abc"
	reason := `Ошибка создания заявки. [GW][332] "Нехватка средств по лимитам клиента."`

	for i := 1; i < loopRejectLimit; i++ {
		if fired, n, _ := g.Reject(cid, reason); fired || n != i {
			t.Fatalf("на %d-м отказе рано останавливать: fired=%v n=%d", i, fired, n)
		}
		if stop, _ := g.Blocked(cid); stop {
			t.Fatalf("источник заблокирован на %d-м отказе", i)
		}
	}
	fired, n, _ := g.Reject(cid, reason)
	if !fired || n != loopRejectLimit {
		t.Fatalf("на пороге обязан сработать: fired=%v n=%d", fired, n)
	}
	if stop, _ := g.Blocked(cid); !stop {
		t.Fatal("источник должен молчать")
	}
	// ...и молчит ТОЛЬКО он: чужая защита не страдает от сломавшегося робота
	if stop, _ := g.Blocked("so:b6e2b07ec8"); stop {
		t.Fatal("заблокирован чужой источник")
	}
	// остывание кончилось — источник снова может пробовать
	now += loopCooldownMs + 1
	if stop, _ := g.Blocked(cid); stop {
		t.Fatal("после остывания блокировки быть не должно")
	}
}

func TestLoopGuardResetsOnSuccessAndOnNewReason(t *testing.T) {
	now := int64(1)
	g := NewLoopGuard(func() int64 { return now })
	cid := "so:abc123:top:42"
	for i := 0; i < loopRejectLimit-1; i++ {
		g.Reject(cid, "нет денег")
	}
	// успешная транзакция = картина мира сошлась, счётчик обнуляется
	g.Accept(cid)
	if fired, n, _ := g.Reject(cid, "нет денег"); fired || n != 1 {
		t.Fatalf("после успеха счёт начинается заново: fired=%v n=%d", fired, n)
	}
	// ДРУГАЯ причина — другой счётчик: это не цикл, это новая беда
	for i := 0; i < loopRejectLimit-1; i++ {
		g.Reject(cid, "нет денег")
	}
	if fired, n, _ := g.Reject(cid, "неверная цена"); fired || n != 1 {
		t.Fatalf("смена причины сбрасывает счёт: fired=%v n=%d", fired, n)
	}
}

func TestLoopSourceGroupsAttemptsNotClientIds(t *testing.T) {
	// client_id уникален на каждую попытку — считать по нему значит никогда не
	// собрать цикл. Источник у робота это робот, у умной заявки — заявка.
	if a, b := loopSource("rr:bot1:7:x"), loopSource("rr:bot1:8:y"); a != b {
		t.Fatalf("попытки одного робота должны слиться: %s vs %s", a, b)
	}
	if a, b := loopSource("so:abc:top:1"), loopSource("so:abc:low:2"); a != b {
		t.Fatalf("стенки одной заявки — один источник: %s vs %s", a, b)
	}
	if loopSource("rr:bot1:7:x") == loopSource("rr:bot2:7:x") {
		t.Fatal("разные роботы не должны сливаться")
	}
	if loopSource("unload10-179") != "manual" {
		t.Fatal("ручная заявка — источник manual")
	}
}
