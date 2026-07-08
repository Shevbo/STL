package link

import (
	"fmt"
	"strings"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/commission"
	"shectory/quik_agent/internal/quikdde"
)

// flushSecurities sends a SecuritiesSnapshot built from the provider. Skipped
// when nothing changed since the last send: with poll_interval_sec=1 the old
// unconditional full snapshot (~500 rows) every second burned STL CPU for no
// information (the source sheets are mostly static, fully static with DDE off).
func (l *Link) flushSecurities(stream quikv1.QuikAgentLink_SessionClient, full bool) error {
	rows := l.opt.Provider.Securities()
	if len(rows) == 0 {
		return nil
	}
	var newest int64
	for _, r := range rows {
		if r.ReceivedUnixMs > newest {
			newest = r.ReceivedUnixMs
		}
	}
	if newest > 0 && newest == l.secSentMs {
		return nil // unchanged since last send
	}
	l.secSentMs = newest
	items := make([]*quikv1.Security, 0, len(rows))
	for _, r := range rows {
		items = append(items, &quikv1.Security{
			Code:             r.Code,
			Name:             r.Name,
			ClassCode:        r.ClassCode,
			PriceStep:        r.PriceStep,
			StepCost:         r.StepCost,
			ReceivedAtUnixMs: r.ReceivedUnixMs,
		})
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_Securities{
			Securities: &quikv1.SecuritiesSnapshot{Items: items, IsFull: full},
		},
	})
}

// flushParams sends a ParamsSnapshot with the commission coefficient per row.
func (l *Link) flushParams(stream quikv1.QuikAgentLink_SessionClient) error {
	rows := l.opt.Provider.Params()
	if len(rows) == 0 {
		return nil
	}
	var newest int64
	for _, r := range rows {
		if r.ReceivedUnixMs > newest {
			newest = r.ReceivedUnixMs
		}
	}
	if newest > 0 && newest == l.paramsSentMs {
		return nil // unchanged since last send
	}
	l.paramsSentMs = newest
	var receivedAt int64
	out := make([]*quikv1.ParamRow, 0, len(rows))
	for _, r := range rows {
		out = append(out, &quikv1.ParamRow{
			Code:      r.Code,
			PriceStep: r.PriceStep,
			StepCost:  r.StepCost,
			Coef:      commission.CoefOrZero(r.PriceStep, r.StepCost),
		})
		if r.ReceivedUnixMs > receivedAt {
			receivedAt = r.ReceivedUnixMs
		}
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_Params{
			Params: &quikv1.ParamsSnapshot{Rows: out, ReceivedAtUnixMs: receivedAt},
		},
	})
}

// flushMarketData sends a MarketDataTick per subscribed code (or all ticks if no
// explicit subscriptions yet), and an OrderBook per subscribed code.
func (l *Link) flushMarketData(stream quikv1.QuikAgentLink_SessionClient) error {
	subs := l.subscribedCodes()

	for _, tk := range l.opt.Provider.Ticks() {
		if len(subs) > 0 {
			if _, ok := subs[tk.Code]; !ok {
				continue
			}
		}
		// Only changed ticks: at a 1s poll, re-sending ~490 unchanged rows every
		// second was pure STL ingest burn.
		if l.tickSentMs != nil && l.tickSentMs[tk.Code] >= tk.ReceivedUnixMs {
			continue
		}
		if err := l.sendTick(stream, tk); err != nil {
			return err
		}
		if l.tickSentMs != nil {
			l.tickSentMs[tk.Code] = tk.ReceivedUnixMs
		}
	}

	sent := map[string]bool{}
	// Explicit subscriptions first.
	for code := range subs {
		if book, ok := l.opt.Provider.OrderBook(code); ok && len(book.Bids)+len(book.Asks) > 0 {
			if err := l.sendOrderBook(stream, book); err != nil {
				return err
			}
			sent[code] = true
		}
	}
	// Auto-detect: a стакан is exported on a sheet named by the instrument code
	// (e.g. "RIU6"). Any non-reserved sheet that parses as a book with at least one
	// level is sent. The level check filters non-book sheets (deals, custom tables)
	// that may have a price column but no bid/ask quantities.
	for _, name := range l.opt.Provider.SheetNames() {
		if sent[name] || quikdde.IsReservedSheet(name) {
			continue
		}
		if book, ok := l.opt.Provider.OrderBook(name); ok && len(book.Bids)+len(book.Asks) > 0 {
			if err := l.sendOrderBook(stream, book); err != nil {
				return err
			}
			sent[name] = true
		}
	}
	// QLua-fed books (the PRIMARY source with DDE retired): the sheet walk above
	// finds nothing when no DDE стакан is exported, so enumerate the lua overlay
	// explicitly — otherwise STL never receives a book at all. Change-gated by
	// CONTENT: the Lua re-publishes an unchanged book every second with a fresh
	// stamp, and re-sending identical levels is pure STL ingest burn.
	for _, code := range l.opt.Provider.LuaBookCodes() {
		if sent[code] {
			continue
		}
		book, ok := l.opt.Provider.OrderBook(code)
		if !ok || len(book.Bids)+len(book.Asks) == 0 {
			continue
		}
		fp := bookFingerprint(book)
		if l.bookSentFp != nil && l.bookSentFp[code] == fp {
			continue
		}
		if err := l.sendOrderBook(stream, book); err != nil {
			return err
		}
		if l.bookSentFp != nil {
			l.bookSentFp[code] = fp
		}
		sent[code] = true
	}
	return nil
}

// bookFingerprint folds a book's levels into a compact content key for the
// change gate (price/qty of every level, both sides).
func bookFingerprint(b quikdde.Book) string {
	var sb strings.Builder
	for _, lv := range b.Bids {
		fmt.Fprintf(&sb, "b%.10g:%d;", lv.Price, lv.Quantity)
	}
	for _, lv := range b.Asks {
		fmt.Fprintf(&sb, "a%.10g:%d;", lv.Price, lv.Quantity)
	}
	return sb.String()
}

// flushRawTables pushes EVERY QUIK DDE sheet to STL as a generic RawTable, so any
// table the user exports (including "deals") shows up downstream without an
// allowlist. This is additive to the typed sends (securities/tick/order_book/
// params); STL dedupes by name. A sheet is only sent when its lastMutationMs
// changed since the last send (first observation always sends), to avoid
// re-streaming unchanged tables. rawSent is reset per session in runOnce.
func (l *Link) flushRawTables(stream quikv1.QuikAgentLink_SessionClient) error {
	if l.rawSent == nil {
		l.rawSent = map[string]int64{}
	}
	for _, name := range l.opt.Provider.SheetNames() {
		columns, dataRows, lastMutationMs := l.opt.Provider.Sheet(name)
		if columns == nil {
			continue // unknown or empty sheet
		}
		if prev, ok := l.rawSent[name]; ok && prev == lastMutationMs {
			continue // unchanged since last send
		}
		rows := make([]*quikv1.TableRow, 0, len(dataRows))
		for _, dr := range dataRows {
			rows = append(rows, &quikv1.TableRow{Cells: dr})
		}
		if err := l.sendMsg(stream, &quikv1.AgentMessage{
			Payload: &quikv1.AgentMessage_RawTable{
				RawTable: &quikv1.RawTable{
					Name:             name,
					Columns:          columns,
					Rows:             rows,
					ReceivedAtUnixMs: lastMutationMs,
				},
			},
		}); err != nil {
			return err
		}
		l.rawSent[name] = lastMutationMs
	}
	return nil
}

func (l *Link) subscribedCodes() map[string]struct{} {
	l.mu.RLock()
	defer l.mu.RUnlock()
	out := make(map[string]struct{}, len(l.subs))
	for k := range l.subs {
		out[k] = struct{}{}
	}
	return out
}

func (l *Link) sendTick(stream quikv1.QuikAgentLink_SessionClient, tk quikdde.Tick) error {
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_Tick{
			Tick: &quikv1.MarketDataTick{
				Code:             tk.Code,
				Last:             tk.Last,
				Bid:              tk.Bid,
				Ask:              tk.Ask,
				OpenInterest:     tk.OpenInterest,
				ReceivedAtUnixMs: tk.ReceivedUnixMs,
			},
		},
	})
}

func (l *Link) sendOrderBook(stream quikv1.QuikAgentLink_SessionClient, book quikdde.Book) error {
	bids := make([]*quikv1.OrderBookLevel, 0, len(book.Bids))
	for _, lv := range book.Bids {
		bids = append(bids, &quikv1.OrderBookLevel{Price: lv.Price, Quantity: lv.Quantity})
	}
	asks := make([]*quikv1.OrderBookLevel, 0, len(book.Asks))
	for _, lv := range book.Asks {
		asks = append(asks, &quikv1.OrderBookLevel{Price: lv.Price, Quantity: lv.Quantity})
	}
	return l.sendMsg(stream, &quikv1.AgentMessage{
		Payload: &quikv1.AgentMessage_OrderBook{
			OrderBook: &quikv1.OrderBook{
				Code:             book.Code,
				Bids:             bids,
				Asks:             asks,
				ReceivedAtUnixMs: book.ReceivedUnixMs,
			},
		},
	})
}
