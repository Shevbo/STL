package runner

import (
	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

// ProviderTicks adapts the quikdde Provider to the bridge's TickSource.
//
// Two sources are MERGED per instrument, freshest wins:
//   - the params/quotes sheet (last/bid/ask/OI) — richest, but QUIK's DDE output
//     of that big table has repeatedly died in production;
//   - the per-code order-book sheets (best bid/ask) — small tables that stay
//     alive, so bars keep building from the mid even when the params sheet dies.
type ProviderTicks struct{ P *quikdde.Provider }

func (pt ProviderTicks) Snapshot() []*quikv1.MarketDataTick {
	// Price step per code (from the params sheet's last-known state) — used to
	// quantize the book mid onto the exchange grid. A half-step "price" like
	// 89175 on a 10-step instrument does not exist and must never leave here.
	steps := map[string]float64{}
	for _, pr := range pt.P.Params() {
		if pr.PriceStep > 0 {
			steps[pr.Code] = pr.PriceStep
		}
	}
	byCode := map[string]*quikv1.MarketDataTick{}
	for _, tk := range pt.P.Ticks() {
		byCode[tk.Code] = &quikv1.MarketDataTick{
			Code:             tk.Code,
			Last:             tk.Last,
			Bid:              tk.Bid,
			Ask:              tk.Ask,
			OpenInterest:     tk.OpenInterest,
			ReceivedAtUnixMs: tk.ReceivedUnixMs,
		}
	}
	// Book-derived ticks: every non-reserved sheet that parses as a стакан.
	for _, name := range pt.P.SheetNames() {
		if quikdde.IsReservedSheet(name) {
			continue
		}
		book, ok := pt.P.OrderBook(name)
		if !ok {
			continue
		}
		var bid, ask float64
		if len(book.Bids) > 0 {
			bid = book.Bids[0].Price
		}
		if len(book.Asks) > 0 {
			ask = book.Asks[0].Price
		}
		if bid <= 0 && ask <= 0 {
			continue
		}
		ex := byCode[name]
		if ex != nil && ex.ReceivedAtUnixMs >= book.ReceivedUnixMs {
			continue // params tick is fresher — keep it
		}
		// A stale last carried from the dead params sheet would win pick_price()
		// forever, so it is dropped. Instead: mid of the book QUANTIZED to the
		// instrument's price step (an off-grid "price" does not exist on the
		// exchange). No known step -> best bid (a real printed level).
		var last float64
		if bid > 0 && ask > 0 {
			mid := (bid + ask) / 2
			if step := steps[name]; step > 0 {
				last = float64(int64(mid/step+0.5)) * step
			} else {
				last = bid
			}
		} else if bid > 0 {
			last = bid
		} else {
			last = ask
		}
		byCode[name] = &quikv1.MarketDataTick{
			Code: name, Last: last, Bid: bid, Ask: ask,
			ReceivedAtUnixMs: book.ReceivedUnixMs,
		}
	}
	out := make([]*quikv1.MarketDataTick, 0, len(byCode))
	for _, tk := range byCode {
		out = append(out, tk)
	}
	return out
}
