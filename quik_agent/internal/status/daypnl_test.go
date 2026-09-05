package status

import (
	"math"
	"testing"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/quikdde"

	quikv1 "shectory/quik_agent/internal/pb"
)

const (
	riCoef = 17.37744 / 10.0
	riLast = 82830.0
	// 04.09.2026 17:00 МСК. Часы обязаны быть настоящими: отсечка дня считает
	// МСК-полночь, а на эпохе 1970 она уходит в минус и сделка со штампом
	// «вчера» проходит фильтр.
	riNow = int64(1_788_530_400_000)
)

// dayDeps: один реальный робот на RIU6 плюс ручная торговля оператора.
func dayDeps(robotPos int64, accNet int64, trades []accounts.Trade, posVM float64, moneyVM float64) Deps {
	d := baseDeps()
	d.NowMs = func() int64 { return riNow }
	d.Provider = fakeProvider{
		ticks:  []quikdde.Tick{{Code: "RIU6", Last: riLast}},
		params: []quikdde.ParamRow{{Code: "RIU6", PriceStep: 10, StepCost: 17.37744}},
	}
	d.Robots = fakeRobots{
		specs:  []*quikv1.RobotSpec{{RobotId: "r1", Symbol: "RIU6", StrategyId: "macd"}},
		paused: map[string]bool{}, times: map[string][2]int64{},
	}
	d.Runner = fakeRunner{statuses: map[string]*quikv1.RobotStatus{
		"r1": {RobotId: "r1", Position: robotPos},
	}}
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		Positions: []accounts.Position{
			{Sec: "RIU6", Net: accNet, VarMargin: posVM, HasVarMargin: true},
		},
		Trades: trades,
		Money:  &accounts.Money{VarMargin: moneyVM},
	}}
	return d
}

func dayTrade(tag, side string, price float64, qty int64, tsMs int64) accounts.Trade {
	return accounts.Trade{Sec: "RIU6", Tag: tag, Side: side, Price: price, Qty: qty, ExchTsMs: tsMs}
}

func sumVM(d dayJSON) (robots, manual float64) {
	for _, c := range d.Classes {
		if c.Kind == "robot" {
			robots += c.VMRub
		} else {
			manual += c.VMRub
		}
	}
	return
}

// Ручная торговля оператора обязана быть ОТДЕЛЬНОЙ строкой, а не растворяться в
// роботах: 04.09.2026 восемь тысяч рублей ручного результата выглядели как
// ошибка учёта робота, потому что показать их было негде.
func TestDayPnL_SplitsManualFromRobots(t *testing.T) {
	now := riNow
	// ВМ счёта = робот (шорт от 82650 против 82830) + ручные (3 по 170 пунктов).
	const quikVM = riCoef * ((82650 - riLast) + 3*(82600-82430))
	d := dayDeps(-1, -1, []accounts.Trade{
		dayTrade("r1", "S", 82650, 1, now),         // робот открыл шорт внутри дня
		dayTrade("", "S", 82600, 3, now),           // оператор руками продал 3
		dayTrade("stl-so-abc", "B", 82430, 3, now), // и закрыл их умной заявкой
	}, quikVM, quikVM)

	got := buildDayJSON(d, d.Accounts.Snapshot())
	robots, manual := sumVM(got)
	wantRobots := riCoef * (82650 - riLast)      // -312.79
	wantManual := riCoef * (3*82600 - 3*82430)   // +886.25
	if math.Abs(robots-wantRobots) > 0.01 {
		t.Errorf("роботы: %.2f, ждали %.2f", robots, wantRobots)
	}
	if math.Abs(manual-wantManual) > 0.01 {
		t.Errorf("ручные: %.2f, ждали %.2f", manual, wantManual)
	}
	if !got.Ok || got.Residual == nil || math.Abs(*got.Residual) > 0.01 {
		t.Errorf("ВМ не сошлась: ok=%v residual=%v sum=%.2f", got.Ok, got.Residual, got.SumRub)
	}
}

// Позиция, перенесённая через клиринг, считается от расчётной цены, а не от
// входа. Расчётную цену QLua не публикует — она решается из ВМ самого QUIK,
// и сумма по участникам обязана сойтись с ВМ счёта ДО КОПЕЙКИ.
func TestDayPnL_CarriedPositionSumsToQuikVM(t *testing.T) {
	now := riNow
	// Робот вошёл в день с -2 и докупил 1; оператор руками продал 1 и держит её.
	// accNet = -2 + 1 - 1 = -2; за окно наторговали +1-1 = 0, значит на начало -2.
	const quikVM = 4_242.42
	d := dayDeps(-1, -2, []accounts.Trade{
		dayTrade("r1", "B", 82500, 1, now),
		dayTrade("", "S", 82700, 1, now),
	}, quikVM, quikVM)

	got := buildDayJSON(d, d.Accounts.Snapshot())
	if !got.Ok {
		t.Fatalf("ok=false, note=%q", got.Note)
	}
	if math.Abs(got.SumRub-quikVM) > 0.01 {
		t.Errorf("сумма участников %.2f != ВМ счёта %.2f", got.SumRub, quikVM)
	}
	if len(got.Secs) != 1 || got.Secs[0].BaseSrc != "quik_vm" {
		t.Errorf("расчётная цена не решена: %+v", got.Secs)
	}
	if got.Secs[0].NetStart != -2 {
		t.Errorf("позиция на начало дня %d, ждали -2", got.Secs[0].NetStart)
	}
}

// Заявка из мобильного приложения брокера вправе нести свой brokerref. Принять
// её за робота значит приписать чужую сделку роботу — то самое, из-за чего эту
// разбивку и завели. Незнакомый тег идёт в ручные, со своим ключом.
func TestDayPnL_UnknownTagIsNotARobot(t *testing.T) {
	d := dayDeps(0, 0, []accounts.Trade{
		dayTrade("FINAM-MOBILE", "S", 82700, 1, riNow),
		dayTrade("FINAM-MOBILE", "B", 82600, 1, riNow),
	}, 0, riCoef*100)

	got := buildDayJSON(d, d.Accounts.Snapshot())
	if len(got.Classes) != 1 {
		t.Fatalf("ждали одну строку, получили %+v", got.Classes)
	}
	if got.Classes[0].Kind != "external" || got.Classes[0].Key != "FINAM-MOBILE" {
		t.Errorf("чужой тег принят за робота: %+v", got.Classes[0])
	}
	if math.Abs(got.Classes[0].VMRub-riCoef*100) > 0.01 {
		t.Errorf("P&L ручной сделки %.2f, ждали %.2f", got.Classes[0].VMRub, riCoef*100)
	}
}

// Сделки прошлой сессии лежат в кольце QUIK ещё сутки; попав в окно, они
// сдвинули бы результат дня. Отсекаются по бирже, как в recon.
func TestDayPnL_DropsPreSessionTrades(t *testing.T) {
	d := dayDeps(0, 0, nil, 0, 0)
	floor := mskMidnightMs(riNow)
	d.Accounts = fakeAccounts{snap: accounts.Snapshot{
		Positions: []accounts.Position{{Sec: "RIU6", Net: 0, VarMargin: 0, HasVarMargin: true}},
		Trades: []accounts.Trade{
			dayTrade("r1", "S", 90000, 5, floor-1),
			dayTrade("r1", "B", 80000, 5, floor-1),
		},
		Money: &accounts.Money{VarMargin: 0},
	}}
	got := buildDayJSON(d, d.Accounts.Snapshot())
	if len(got.Classes) != 0 || got.SumRub != 0 {
		t.Errorf("вчерашние сделки попали в день: %+v", got.Classes)
	}
}
