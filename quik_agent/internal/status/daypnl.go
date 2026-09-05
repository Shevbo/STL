package status

import (
	"math"
	"sort"

	"shectory/quik_agent/internal/accounts"
)

// Вариационная маржа счёта, разложенная по тем, кто её сделал.
//
// Зачем. Панель показывала «сегодня» по каждому роботу и ВМ счёта рядом, и они
// не сходились: 04.09.2026 ВМ была +16 248 ₽, а роботы вместе давали +5 074 ₽.
// Разница — РУЧНАЯ торговля оператора (свои заявки в терминале, умные заявки,
// align-заявки recon). Её не было видно нигде: агент отдаёт в статусе только
// последние 100 сделок QUIK, а журнал algo_trades по построению знает лишь
// роботов. Восемь тысяч рублей за день выглядели как ошибка учёта робота.
//
// Что считаем. Финрез за окно по рыночной переоценке (mark-to-market) для
// участника x:
//
//	VM_x = coef * ( cash_x + net_end_x * last - net_start_x * base )
//	cash_x = Σ(продажа: +цена*кол-во) - Σ(покупка: цена*кол-во)   — в пунктах
//
// Это ровно то же, что «фикс + переоценка» на карточке робота: разложение по
// средней цене телескопируется в денежный поток. Складывая по всем участникам
// (роботы + ручные), получаем ВМ счёта — тождество, а не совпадение.
//
// base — расчётная цена клиринга, от которой QUIK считает ВМ перенесённой
// позиции. QLua её не публикует, поэтому РЕШАЕМ её из самой ВМ, которую QUIK
// уже отдал по инструменту (futures_client_holding.varmargin). Тогда сумма
// сходится до копейки при любом окне, а окно влияет только на то, кому из
// участников достанется перенос. Позиции не было на начало окна — base не
// нужен вовсе.
//
// Окно — MSK-полночь, как у recon, и это НЕ приближение: ВМ счёта живёт от
// вечернего клиринга (19:00), а QUIK датирует вечерние сделки СЛЕДУЮЩИМ
// торговым днём (тот же механизм, что залил вчерашний вечер в journal-sync
// 2026-08). По бирже они падают за полночь и в окно попадают — то есть окно
// совпадает с торговым днём FORTS само собой.
//
// ponytail: если окно всё же разъедется с клирингом, перенос сдвинется МЕЖДУ
// участниками, но не изменит сумму — она решается из ВМ самого QUIK. Точный
// base появится, когда QLua начнёт слать SETTLEPRICE.
type dayClassJSON struct {
	Key      string  `json:"key"`  // robot_id, либо вид ручной торговли
	Kind     string  `json:"kind"` // robot | terminal | smart | recon
	Sec      string  `json:"sec"`
	Fills    int     `json:"fills"`
	Lots     int64   `json:"lots"`
	CashPts  float64 `json:"cash_pts"`
	NetStart int64   `json:"net_start"`
	NetEnd   int64   `json:"net_end"`
	VMRub    float64 `json:"vm_rub"`
}

type daySecJSON struct {
	Code     string  `json:"code"`
	Last     float64 `json:"last"`
	Coef     float64 `json:"coef"`
	Base     float64 `json:"base"`
	BaseSrc  string  `json:"base_src"` // quik_vm | none | unresolved
	NetStart int64   `json:"net_start"`
	NetEnd   int64   `json:"net_end"`
}

type dayJSON struct {
	Ok      bool           `json:"ok"`
	FromMs  int64          `json:"from_ms"`
	Classes []dayClassJSON `json:"classes"`
	Secs    []daySecJSON   `json:"secs"`
	// SumRub — сумма по участникам, QuikVM — ВМ счёта из QUIK, Residual — их
	// разница. Ненулевой остаток значит, что часть сделок дня не доехала (ринг
	// сделок обрезан) — печатать его, а не прятать.
	SumRub   float64  `json:"sum_rub"`
	QuikVM   *float64 `json:"quik_vm"`
	Residual *float64 `json:"residual"`
	Note     string   `json:"note,omitempty"`
}

// classOf: чей это тег. Робот стамповал в комментарий заявки свой ID, align —
// "recon", умная заявка — "stl-so-<id>". Пустой тег — торговля оператора мимо
// агента: терминал QUIK или мобильное приложение брокера, они комментарий не
// заполняют.
//
// Незнакомый НЕПУСТОЙ тег объявляем «прочим», а НЕ роботом. Мобильное
// приложение Finam вправе поставить свой brokerref, и трактовка «раз тег есть,
// значит робот» приписала бы сделку оператора конкретному роботу — ровно тот
// вид ошибки, из-за которого и завели эту разбивку. Снятый робот тоже попадёт
// сюда, но со СВОИМ ключом: он виден в строке, а не растворён.
func classOf(tag string, robots map[string]bool) (key, kind string) {
	switch {
	case tag == "":
		return "terminal", "terminal"
	case robots[tag]:
		return tag, "robot"
	case tag == "recon":
		return "recon", "recon"
	case len(tag) >= 6 && tag[:6] == "stl-so":
		return "smart", "smart"
	default:
		return tag, "external"
	}
}

func buildDayJSON(d Deps, acc accounts.Snapshot) dayJSON {
	out := dayJSON{FromMs: mskMidnightMs(d.nowMs())}

	coef := map[string]float64{}
	for _, pr := range d.Provider.Params() {
		if pr.PriceStep > 0 && pr.StepCost > 0 {
			coef[pr.Code] = pr.StepCost / pr.PriceStep
		}
	}
	last := map[string]float64{}
	for _, t := range d.Provider.Ticks() {
		if t.Last > 0 {
			last[t.Code] = t.Last
		}
	}

	// Позиция сейчас: у роботов — своя, у ручной торговли — всё остальное на счёте.
	robotNet := map[string]map[string]int64{} // sec -> robot -> net
	realRobots := map[string]bool{}
	st := d.Runner.LastStatuses()
	for _, spec := range d.Robots.All() {
		if spec.GetPaper() {
			continue // бумажный робот на счёт не выходит
		}
		id := spec.GetRobotId()
		realRobots[id] = true
		sec := spec.GetSymbol()
		if robotNet[sec] == nil {
			robotNet[sec] = map[string]int64{}
		}
		robotNet[sec][id] = st[id].GetPosition()
	}
	accNet := map[string]int64{}
	quikVM := map[string]float64{}
	hasVM := map[string]bool{}
	for _, p := range acc.Positions {
		accNet[p.Sec] = p.Net
		if p.HasVarMargin {
			quikVM[p.Sec], hasVM[p.Sec] = p.VarMargin, true
		}
	}

	type agg struct {
		key, kind, sec string
		fills          int
		lots           int64
		cash           float64
		signed         int64 // изменение позиции за окно (покупка +, продажа -)
	}
	byKey := map[string]*agg{}
	secCash := map[string]float64{}
	secSigned := map[string]int64{}
	for _, t := range acc.Trades {
		ts := t.TsMs
		if t.ExchTsMs > 0 {
			ts = t.ExchTsMs
		}
		if ts < out.FromMs || t.Qty == 0 {
			continue
		}
		key, kind := classOf(t.Tag, realRobots)
		id := kind + "\x00" + key + "\x00" + t.Sec
		a := byKey[id]
		if a == nil {
			a = &agg{key: key, kind: kind, sec: t.Sec}
			byKey[id] = a
		}
		signed, cash := t.Qty, -t.Price*float64(t.Qty)
		if t.Side == "S" {
			signed, cash = -t.Qty, t.Price*float64(t.Qty)
		}
		a.fills++
		a.lots += t.Qty
		a.cash += cash
		a.signed += signed
		secCash[t.Sec] += cash
		secSigned[t.Sec] += signed
	}

	// Позиция на начало окна = позиция сейчас минус всё, что наторговали в окне.
	// Считаем на уровне инструмента (для base) и на уровне участника (для его ВМ).
	netEnd := func(a *agg) int64 {
		if a.kind == "robot" {
			return robotNet[a.sec][a.key]
		}
		var robots int64
		for _, n := range robotNet[a.sec] {
			robots += n
		}
		manual := accNet[a.sec] - robots
		// Вся ручная торговля инструмента делится между terminal/smart/recon.
		// Позицию отдаём тому, кто в окне её и набрал; если набирали несколько,
		// делим пропорционально их изменению — точнее данных у нас нет.
		var manualSigned int64
		for _, o := range byKey {
			if o.sec == a.sec && o.kind != "robot" {
				manualSigned += o.signed
			}
		}
		if manualSigned == 0 {
			return manual
		}
		return int64(math.Round(float64(manual) * float64(a.signed) / float64(manualSigned)))
	}

	// base по инструменту: решаем из ВМ, которую QUIK уже посчитал.
	base := map[string]float64{}
	baseSrc := map[string]string{}
	secNetStart := map[string]int64{}
	for sec := range secCash {
		c, lp := coef[sec], last[sec]
		ns := accNet[sec] - secSigned[sec]
		secNetStart[sec] = ns
		switch {
		case ns == 0:
			base[sec], baseSrc[sec] = lp, "none" // переносить нечего, base не влияет
		case hasVM[sec] && c > 0:
			b := (secCash[sec] + float64(accNet[sec])*lp - quikVM[sec]/c) / float64(ns)
			if lp > 0 && math.Abs(b/lp-1) > 0.05 {
				base[sec], baseSrc[sec] = lp, "unresolved" // решение вне рынка: окно не то
			} else {
				base[sec], baseSrc[sec] = b, "quik_vm"
			}
		default:
			base[sec], baseSrc[sec] = lp, "unresolved"
		}
	}

	out.Ok = true
	for _, a := range byKey {
		c, lp := coef[a.sec], last[a.sec]
		if c <= 0 || lp <= 0 {
			out.Ok = false
			continue
		}
		ne := netEnd(a)
		ns := ne - a.signed
		row := dayClassJSON{
			Key: a.key, Kind: a.kind, Sec: a.sec, Fills: a.fills, Lots: a.lots,
			CashPts: a.cash, NetStart: ns, NetEnd: ne,
			VMRub: c * (a.cash + float64(ne)*lp - float64(ns)*base[a.sec]),
		}
		out.SumRub += row.VMRub
		out.Classes = append(out.Classes, row)
	}
	sort.Slice(out.Classes, func(i, j int) bool {
		if out.Classes[i].Kind != out.Classes[j].Kind {
			return out.Classes[i].Kind < out.Classes[j].Kind
		}
		return out.Classes[i].Key < out.Classes[j].Key
	})
	for sec := range secCash {
		if baseSrc[sec] == "unresolved" {
			out.Ok = false
		}
		out.Secs = append(out.Secs, daySecJSON{
			Code: sec, Last: last[sec], Coef: coef[sec], Base: base[sec],
			BaseSrc: baseSrc[sec], NetStart: secNetStart[sec], NetEnd: accNet[sec],
		})
	}
	sort.Slice(out.Secs, func(i, j int) bool { return out.Secs[i].Code < out.Secs[j].Code })

	if m := acc.Money; m != nil {
		vm := m.VarMargin
		res := vm - out.SumRub
		out.QuikVM, out.Residual = &vm, &res
		if math.Abs(res) > 1 {
			out.Ok = false
			out.Note = "часть сделок дня не видна агенту"
		}
	}
	if !out.Ok && out.Note == "" {
		out.Note = "нет цены или шага инструмента"
	}
	return out
}
