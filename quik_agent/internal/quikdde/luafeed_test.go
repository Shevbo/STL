package quikdde

import (
	"testing"
	"time"
)

func TestLuaTickOverlayAddsAndWins(t *testing.T) {
	p := NewProvider()
	// no sheets at all -> lua ticks still flow (DDE fully optional)
	p.SetLuaTick("RIU6", 89000, 88990, 89010)
	ticks := p.Ticks()
	if len(ticks) != 1 || ticks[0].Code != "RIU6" || ticks[0].Last != 89000 {
		t.Fatalf("lua-only ticks = %+v", ticks)
	}
	if ticks[0].ReceivedUnixMs == 0 {
		t.Fatal("lua tick must be recv-stamped")
	}
	// liveness: LastMutationMs must see the lua feed
	if p.LastMutationMs() == 0 {
		t.Fatal("LastMutationMs must include lua feed")
	}
}

func TestLuaBookCodesEnumeratesOverlay(t *testing.T) {
	p := NewProvider()
	if got := p.LuaBookCodes(); len(got) != 0 {
		t.Fatalf("empty provider must list no book codes, got %v", got)
	}
	p.SetLuaBook("RIU6", []BookLevel{{Price: 88990, Quantity: 3}}, nil)
	p.SetLuaBook("SiU6", nil, []BookLevel{{Price: 76761, Quantity: 5}})
	got := map[string]bool{}
	for _, c := range p.LuaBookCodes() {
		got[c] = true
	}
	// With DDE retired the link walks THESE codes; missing one means STL never
	// receives that instrument's стакан (the exact live gap this fixes).
	if !got["RIU6"] || !got["SiU6"] || len(got) != 2 {
		t.Fatalf("LuaBookCodes = %v", got)
	}
}

func TestLuaBookOverlayWinsOverStaleSheet(t *testing.T) {
	p := NewProvider()
	p.SetLuaBook("RIU6",
		[]BookLevel{{Price: 88990, Quantity: 3}},
		[]BookLevel{{Price: 89010, Quantity: 2}})
	b, ok := p.OrderBook("RIU6")
	if !ok || b.Bids[0].Price != 88990 || b.Asks[0].Price != 89010 {
		t.Fatalf("lua book = %+v ok=%v", b, ok)
	}
	if time.Now().UnixMilli()-b.ReceivedUnixMs > 5000 {
		t.Fatal("lua book must be freshly stamped")
	}
}
