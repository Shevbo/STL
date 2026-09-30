package trade

import (
	"testing"

	"shectory/quik_agent/internal/quikdde"
)

// Стакан инцидента 30.09.2026: рынок 85490/85510, а стенка треугольника
// посчиталась на 85000 — на 0.59% ПО ТУ СТОРОНУ при колларе 0.2%.
func incidentBook(nowMs int64) quikdde.Book {
	b := book(
		[][2]float64{{85490, 20}, {85480, 30}},
		[][2]float64{{85510, 18}, {85520, 25}},
	)
	b.Code = "RIZ6"
	b.ReceivedUnixMs = nowMs
	return b
}

func collarManager(bk quikdde.Book, nowMs int64) *Manager {
	m := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	m.book = staticBook{b: bk}
	m.nowMsFn = func() int64 { return nowMs }
	return m
}

// ГЛАВНЫЙ УРОК 30.09.2026. Эта проверка отбила бы все 43 заявки на уровне
// агента, независимо от ошибки в STL. Коллар был написан и подключён ТОЛЬКО к
// переносу заявки; постановка — главный путь — шла мимо него.
func TestPlaceCollarRejectsOrderThatCrossesTheMarket(t *testing.T) {
	const now = 1_790_800_000_000
	m := collarManager(incidentBook(now), now)

	ok, reason := m.checkPlaceCollar("RIZ6", false, 85000) // продажа глубоко под рынком
	if ok {
		t.Fatal("продажа по 85000 при спросе 85490 обязана быть отбита: она исполнится мгновенно")
	}
	if reason == "" {
		t.Fatal("причина отказа обязана быть названа")
	}
}

// Погоня умного исполнения встаёт на встречную котировку. Это законно и
// обязано проходить, иначе гарантия выхода сломана.
func TestPlaceCollarAllowsAggressiveChase(t *testing.T) {
	const now = 1_790_800_000_000
	m := collarManager(incidentBook(now), now)

	if ok, _ := m.checkPlaceCollar("RIZ6", false, 85490); !ok {
		t.Fatal("продажа по лучшему спросу — это погоня, она законна")
	}
	if ok, _ := m.checkPlaceCollar("RIZ6", true, 85510); !ok {
		t.Fatal("покупка по лучшему предложению — это погоня, она законна")
	}
}

// Мирная заявка, ждущая свою цену, коллара не касается: она НЕ пересекает рынок.
func TestPlaceCollarAllowsRestingOrders(t *testing.T) {
	const now = 1_790_800_000_000
	m := collarManager(incidentBook(now), now)

	if ok, _ := m.checkPlaceCollar("RIZ6", false, 86000); !ok {
		t.Fatal("продажа ВЫШЕ рынка — обычная стенка коридора")
	}
	if ok, _ := m.checkPlaceCollar("RIZ6", true, 84000); !ok {
		t.Fatal("покупка НИЖЕ рынка — обычная стенка коридора")
	}
}

// Без стакана и на протухшем стакане проверка пропускается, а не запрещает:
// запертая защитная заявка опаснее пропущенной проверки. Страхует экспозиция.
func TestPlaceCollarSkipsWithoutUsableBook(t *testing.T) {
	const now = 1_790_800_000_000
	m := collarManager(incidentBook(now-staleBookMs-1), now)
	if ok, _ := m.checkPlaceCollar("RIZ6", false, 85000); !ok {
		t.Fatal("на протухшем стакане судить нельзя — обязаны пропустить")
	}

	m2 := NewManager(ManagerConfig{}, nil, NewGuard(baseLimits()), nil, nil)
	if ok, _ := m2.checkPlaceCollar("RIZ6", false, 85000); !ok {
		t.Fatal("без источника стакана обязаны пропустить")
	}
}

// СВИП ПО ГЕОМЕТРИИ, А НЕ ПРИМЕР. Именно этого теста не хватило 30.09: тесты
// проверяли поведение («заявка переставилась на +100»), а фикстура всегда
// строила рынок ВНУТРИ фигуры. Баг жил ровно в той геометрии, которую
// фикстура не умела построить. Утверждение здесь — ЗАПРЕТ, и он перебирает
// состояния, а не ожидает одно.
func TestPlaceCollarInvariantOverPriceGrid(t *testing.T) {
	const now = 1_790_800_000_000
	bk := incidentBook(now)
	m := collarManager(bk, now)
	bid, ask := bk.Bids[0].Price, bk.Asks[0].Price
	frac := baseLimits().PriceCollarFrac

	for px := 80000.0; px <= 90000.0; px += 10 {
		for _, buy := range []bool{true, false} {
			ok, _ := m.checkPlaceCollar("RIZ6", buy, px)
			// ИНВАРИАНТ: пропущена может быть только цена, не уходящая за
			// встречную котировку дальше коллара. Ни одна цена вне этого
			// коридора не имеет права пройти — при любом сочетании.
			var sane bool
			if buy {
				sane = px <= ask*(1+frac)
			} else {
				sane = px >= bid*(1-frac)
			}
			if ok != sane {
				t.Fatalf("цена %.0f (buy=%v): пропущена=%v, а по инварианту должна быть %v "+
					"(спрос %.0f, предложение %.0f, коллар %.3f)", px, buy, ok, sane, bid, ask, frac)
			}
		}
	}
}
