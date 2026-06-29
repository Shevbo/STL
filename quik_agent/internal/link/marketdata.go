package link

import (
	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/commission"
	"shectory/quik_agent/internal/quikdde"
)

// flushSecurities sends a SecuritiesSnapshot built from the provider.
func (l *Link) flushSecurities(stream quikv1.QuikAgentLink_SessionClient, full bool) error {
	rows := l.opt.Provider.Securities()
	if len(rows) == 0 {
		return nil
	}
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
		if err := l.sendTick(stream, tk); err != nil {
			return err
		}
	}

	for code := range subs {
		if book, ok := l.opt.Provider.OrderBook(code); ok {
			if err := l.sendOrderBook(stream, book); err != nil {
				return err
			}
		}
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
