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
