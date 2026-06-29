package quikdde

import (
	"sort"
	"strings"
	"time"
)

// Plain data types exposed by the provider. internal/link converts these into the
// protobuf wire messages. Keeping them dependency-free lets quikdde build and test
// without the generated pb package.

// SecurityRow is one row of the securities reference (price step / step cost).
type SecurityRow struct {
	Code           string
	Name           string
	ClassCode      string
	PriceStep      float64
	StepCost       float64
	ReceivedUnixMs int64
}

// Tick is a last/quote/open-interest sample for one instrument.
type Tick struct {
	Code           string
	Last           float64
	Bid            float64
	Ask            float64
	OpenInterest   int64
	ReceivedUnixMs int64
}

// BookLevel is one price level in the order book.
type BookLevel struct {
	Price    float64
	Quantity int64
}

// Book is the top-N order book for one instrument.
type Book struct {
	Code           string
	Bids           []BookLevel
	Asks           []BookLevel
	ReceivedUnixMs int64
}

// ParamRow carries the commission inputs for one instrument.
type ParamRow struct {
	Code           string
	PriceStep      float64
	StepCost       float64
	ReceivedUnixMs int64
}

// Conventional QUIK list names. QUIK is configured to export tables under these
// item/list names; override per deployment via the DDE topic if needed.
const (
	SheetParams     = "params"
	SheetSecurities = "securities"
	SheetQuotes     = "quotes"
	SheetOrderBook  = "orderbook"
)

// IsReservedSheet reports whether name is one of the typed sheet names handled by
// the structured views (securities/quotes/params/orderbook). The generic order-book
// auto-detection skips these so they are never mistaken for a per-code стакан.
func IsReservedSheet(name string) bool {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case SheetParams, SheetSecurities, SheetQuotes, SheetOrderBook:
		return true
	}
	return false
}

// Securities returns the securities reference built from the "securities" sheet
// (falls back to the "params" sheet, which on FORTS also carries step columns).
func (p *Provider) Securities() []SecurityRow {
	columns, rows, ts := p.Sheet(SheetSecurities)
	if columns == nil {
		columns, rows, ts = p.Sheet(SheetParams)
	}
	if columns == nil {
		return nil
	}
	codeC := colIndex(columns, "код", "code", "бумаг", "инструмент")
	nameC := colIndex(columns, "наимен", "name", "назван")
	classC := colIndex(columns, "класс", "class")
	stepC := colIndex(columns, "шаг цены", "мин. шаг", "price step", "pricestep")
	costC := colIndex(columns, "ст. шага", "стоимость шага", "step cost", "stepcost")
	if codeC < 0 {
		return nil
	}
	out := make([]SecurityRow, 0, len(rows))
	for _, row := range rows {
		code := cellAt(row, codeC)
		if code == "" {
			continue
		}
		sr := SecurityRow{Code: code, ReceivedUnixMs: ts}
		sr.Name = cellAt(row, nameC)
		sr.ClassCode = cellAt(row, classC)
		if v, ok := parseNum(cellAt(row, stepC)); ok {
			sr.PriceStep = v
		}
		if v, ok := parseNum(cellAt(row, costC)); ok {
			sr.StepCost = v
		}
		out = append(out, sr)
	}
	return out
}

// Params returns commission inputs (price step / step cost) per instrument.
func (p *Provider) Params() []ParamRow {
	columns, rows, ts := p.Sheet(SheetParams)
	if columns == nil {
		return nil
	}
	codeC := colIndex(columns, "код", "code", "бумаг", "инструмент")
	stepC := colIndex(columns, "шаг цены", "мин. шаг", "price step", "pricestep")
	costC := colIndex(columns, "ст. шага", "стоимость шага", "step cost", "stepcost")
	if codeC < 0 || stepC < 0 || costC < 0 {
		return nil
	}
	out := make([]ParamRow, 0, len(rows))
	for _, row := range rows {
		code := cellAt(row, codeC)
		if code == "" {
			continue
		}
		pr := ParamRow{Code: code, ReceivedUnixMs: ts}
		if v, ok := parseNum(cellAt(row, stepC)); ok {
			pr.PriceStep = v
		}
		if v, ok := parseNum(cellAt(row, costC)); ok {
			pr.StepCost = v
		}
		out = append(out, pr)
	}
	return out
}

// Ticks returns last/bid/ask/OI samples from the "quotes" sheet (falls back to
// "params", whose FORTS layout usually includes last/bid/ask/OI columns).
func (p *Provider) Ticks() []Tick {
	columns, rows, ts := p.Sheet(SheetQuotes)
	if columns == nil {
		columns, rows, ts = p.Sheet(SheetParams)
	}
	if columns == nil {
		return nil
	}
	codeC := colIndex(columns, "код", "code", "бумаг", "инструмент")
	lastC := colIndex(columns, "последн", "last", "цена послед")
	bidC := colIndex(columns, "спрос", "bid", "лучший спрос", "покупка")
	askC := colIndex(columns, "предложен", "ask", "offer", "продажа")
	oiC := colIndex(columns, "откр. позиц", "открытые позиц", "open interest", "oi")
	if codeC < 0 {
		return nil
	}
	out := make([]Tick, 0, len(rows))
	for _, row := range rows {
		code := cellAt(row, codeC)
		if code == "" {
			continue
		}
		tk := Tick{Code: code, ReceivedUnixMs: ts}
		if v, ok := parseNum(cellAt(row, lastC)); ok {
			tk.Last = v
		}
		if v, ok := parseNum(cellAt(row, bidC)); ok {
			tk.Bid = v
		}
		if v, ok := parseNum(cellAt(row, askC)); ok {
			tk.Ask = v
		}
		if v, ok := parseInt(cellAt(row, oiC)); ok {
			tk.OpenInterest = v
		}
		out = append(out, tk)
	}
	return out
}

// OrderBook returns the top-N book for one code from the "orderbook" sheet.
// QUIK's стакан export is a flat table of (price, bid qty, ask qty) rows; a level
// with a non-zero bid quantity is a bid, a non-zero ask quantity is an ask.
func (p *Provider) OrderBook(code string) (Book, bool) {
	// Practical naming: a стакан is exported on a sheet named by the instrument
	// code (e.g. "RIU6"), since one fixed "orderbook" sheet cannot hold many books.
	// Read the per-code sheet first; fall back to the legacy "orderbook" sheet.
	columns, rows, ts := p.Sheet(code)
	if columns == nil {
		columns, rows, ts = p.Sheet(SheetOrderBook)
	}
	if columns == nil {
		return Book{}, false
	}
	priceC := colIndex(columns, "цена", "price")
	bidQC := colIndex(columns, "спрос", "bid", "купить", "покупка")
	askQC := colIndex(columns, "предложен", "ask", "продать", "продажа", "offer")
	if priceC < 0 {
		return Book{}, false
	}
	b := Book{Code: code, ReceivedUnixMs: ts}
	for _, row := range rows {
		price, ok := parseNum(cellAt(row, priceC))
		if !ok {
			continue
		}
		if q, ok := parseInt(cellAt(row, bidQC)); ok && q > 0 {
			b.Bids = append(b.Bids, BookLevel{Price: price, Quantity: q})
		}
		if q, ok := parseInt(cellAt(row, askQC)); ok && q > 0 {
			b.Asks = append(b.Asks, BookLevel{Price: price, Quantity: q})
		}
	}
	// Best-first: bids descending by price, asks ascending by price.
	sort.SliceStable(b.Bids, func(i, j int) bool { return b.Bids[i].Price > b.Bids[j].Price })
	sort.SliceStable(b.Asks, func(i, j int) bool { return b.Asks[i].Price < b.Asks[j].Price })
	if len(b.Bids) == 0 && len(b.Asks) == 0 {
		return Book{}, false
	}
	return b, true
}

// FreshnessMs returns how stale the freshest sheet is, in ms (0 if no data yet).
func (p *Provider) FreshnessMs() int64 {
	last := p.LastMutationMs()
	if last == 0 {
		return 0
	}
	age := time.Now().UnixMilli() - last
	if age < 0 {
		return 0
	}
	return age
}
