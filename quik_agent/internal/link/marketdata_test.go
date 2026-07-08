package link

import (
	"testing"

	"shectory/quik_agent/internal/quikdde"
)

func TestBookFingerprintChangesOnAnyLevelChange(t *testing.T) {
	b := quikdde.Book{Code: "RIU6",
		Bids: []quikdde.BookLevel{{Price: 88990, Quantity: 3}, {Price: 88980, Quantity: 1}},
		Asks: []quikdde.BookLevel{{Price: 89010, Quantity: 2}}}
	fp := bookFingerprint(b)
	if fp == "" {
		t.Fatal("fingerprint of a non-empty book must be non-empty")
	}
	// identical content (fresh stamp) -> same fp: the gate must SKIP re-sends,
	// the Lua re-publishes an unchanged book every second with a new ts
	b2 := b
	b2.ReceivedUnixMs = b.ReceivedUnixMs + 1000
	if bookFingerprint(b2) != fp {
		t.Fatal("fingerprint must ignore the receive stamp")
	}
	// any level change -> different fp
	qty := b
	qty.Bids = []quikdde.BookLevel{{Price: 88990, Quantity: 4}, {Price: 88980, Quantity: 1}}
	if bookFingerprint(qty) == fp {
		t.Fatal("qty change must change the fingerprint")
	}
	price := b
	price.Asks = []quikdde.BookLevel{{Price: 89020, Quantity: 2}}
	if bookFingerprint(price) == fp {
		t.Fatal("price change must change the fingerprint")
	}
	// side matters: a bid level must not collide with an equal ask level
	sides := quikdde.Book{
		Bids: []quikdde.BookLevel{{Price: 89000, Quantity: 1}}}
	flipped := quikdde.Book{
		Asks: []quikdde.BookLevel{{Price: 89000, Quantity: 1}}}
	if bookFingerprint(sides) == bookFingerprint(flipped) {
		t.Fatal("bid and ask sides must fingerprint differently")
	}
}
