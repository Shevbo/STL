package quikdde

import "time"

// Lua-feed overlay: market data pushed by the QLua bridge (getParamEx /
// getQuoteLevel2) — the PRIMARY source for the trading path. The DDE sheets
// remain as a fallback/passthrough: DDE requires a manual "Начать вывод" in
// QUIK, dies on agent restarts and on big tables, and has repeatedly gone
// silent in production. Readers (Ticks/OrderBook/FreshnessMs) merge both
// sources, freshest wins, so either feed alone keeps the agent alive.

type luaTick struct {
	last, bid, ask float64
	recvMs         int64
}

// SetLuaTick stores the freshest QLua tick for a code (recv-stamped here).
func (p *Provider) SetLuaTick(code string, last, bid, ask float64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.luaTicks == nil {
		p.luaTicks = map[string]luaTick{}
	}
	p.luaTicks[code] = luaTick{last: last, bid: bid, ask: ask, recvMs: time.Now().UnixMilli()}
}

// SetLuaBook stores the freshest QLua L2 snapshot for a code.
func (p *Provider) SetLuaBook(code string, bids, asks []BookLevel) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.luaBooks == nil {
		p.luaBooks = map[string]Book{}
	}
	p.luaBooks[code] = Book{Code: code, Bids: bids, Asks: asks,
		ReceivedUnixMs: time.Now().UnixMilli()}
}

// luaTicksMerged overlays lua ticks onto a sheet-derived tick list: a lua tick
// replaces a staler sheet tick and adds codes the sheets do not carry.
func (p *Provider) luaTicksMerged(sheet []Tick) []Tick {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if len(p.luaTicks) == 0 {
		return sheet
	}
	byCode := map[string]int{}
	for i, tk := range sheet {
		byCode[tk.Code] = i
	}
	for code, lt := range p.luaTicks {
		tk := Tick{Code: code, Last: lt.last, Bid: lt.bid, Ask: lt.ask, ReceivedUnixMs: lt.recvMs}
		if i, ok := byCode[code]; ok {
			if sheet[i].ReceivedUnixMs < lt.recvMs {
				sheet[i] = tk
			}
		} else {
			sheet = append(sheet, tk)
		}
	}
	return sheet
}

// luaBook returns the lua L2 for a code when it is fresher than sheetMs.
func (p *Provider) luaBook(code string, sheetMs int64) (Book, bool) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	b, ok := p.luaBooks[code]
	if !ok || b.ReceivedUnixMs <= sheetMs {
		return Book{}, false
	}
	return b, true
}

// luaFreshestMs returns the newest lua-feed timestamp (0 when no lua data).
func (p *Provider) luaFreshestMs() int64 {
	p.mu.RLock()
	defer p.mu.RUnlock()
	var newest int64
	for _, t := range p.luaTicks {
		if t.recvMs > newest {
			newest = t.recvMs
		}
	}
	for _, b := range p.luaBooks {
		if b.ReceivedUnixMs > newest {
			newest = b.ReceivedUnixMs
		}
	}
	return newest
}
