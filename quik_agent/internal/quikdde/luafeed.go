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

type luaParam struct {
	priceStep, stepCost, margin float64
	recvMs                      int64
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

// LuaBookCodes lists the codes with a QLua L2 snapshot. The link's book
// forwarder must enumerate THESE: with DDE retired there are no book sheets,
// so a sheet-name walk alone finds nothing and STL never gets a стакан.
func (p *Provider) LuaBookCodes() []string {
	p.mu.RLock()
	defer p.mu.RUnlock()
	codes := make([]string, 0, len(p.luaBooks))
	for code := range p.luaBooks {
		codes = append(codes, code)
	}
	return codes
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


// SetLuaLast updates ONLY the last-trade price of a code's lua tick (tape-driven),
// preserving the freshest bid/ask from the md publisher.
func (p *Provider) SetLuaLast(code string, price float64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.luaTicks == nil {
		p.luaTicks = map[string]luaTick{}
	}
	t := p.luaTicks[code]
	t.last = price
	t.recvMs = time.Now().UnixMilli()
	p.luaTicks[code] = t
}

// SetLuaParam stores instrument reference params from the QLua publisher —
// with these, the DDE params sheet is no longer needed at all.
func (p *Provider) SetLuaParam(code string, priceStep, stepCost, margin float64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.luaParams == nil {
		p.luaParams = map[string]luaParam{}
	}
	p.luaParams[code] = luaParam{priceStep: priceStep, stepCost: stepCost,
		margin: margin, recvMs: time.Now().UnixMilli()}
}

// luaParamsMerged overlays lua params onto the sheet-derived rows (fresher wins,
// absent codes added) — same contract as luaTicksMerged.
func (p *Provider) luaParamsMerged(sheet []ParamRow) []ParamRow {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if len(p.luaParams) == 0 {
		return sheet
	}
	byCode := map[string]int{}
	for i, r := range sheet {
		byCode[r.Code] = i
	}
	for code, lp := range p.luaParams {
		row := ParamRow{Code: code, PriceStep: lp.priceStep, StepCost: lp.stepCost,
			ReceivedUnixMs: lp.recvMs}
		if i, ok := byCode[code]; ok {
			if sheet[i].ReceivedUnixMs < lp.recvMs {
				sheet[i] = row
			}
		} else {
			sheet = append(sheet, row)
		}
	}
	return sheet
}
