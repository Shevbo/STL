package runner

import (
	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
)

// ProviderTicks adapts the quikdde Provider to the bridge's TickSource. Same
// read path the STL link uses (Provider.Ticks) — no second tick pipeline.
type ProviderTicks struct{ P *quikdde.Provider }

func (pt ProviderTicks) Snapshot() []*quikv1.MarketDataTick {
	ticks := pt.P.Ticks()
	out := make([]*quikv1.MarketDataTick, 0, len(ticks))
	for _, tk := range ticks {
		out = append(out, &quikv1.MarketDataTick{
			Code:             tk.Code,
			Last:             tk.Last,
			Bid:              tk.Bid,
			Ask:              tk.Ask,
			OpenInterest:     tk.OpenInterest,
			ReceivedAtUnixMs: tk.ReceivedUnixMs,
		})
	}
	return out
}
