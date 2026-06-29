package quikdde

import (
	"testing"
)

// Examples from the Microsoft Excel SDK (dde.doc / XlTable).

func TestDecodeXlTable_Example1_EastWestNorth(t *testing.T) {
	raw := []byte{
		0x10, 0x00, 0x04, 0x00, 0x01, 0x00, 0x03, 0x00,
		0x02, 0x00, 0x10, 0x00,
		0x04, 0x45, 0x61, 0x73, 0x74,
		0x04, 0x57, 0x65, 0x73, 0x74,
		0x05, 0x4e, 0x6f, 0x72, 0x74, 0x68,
	}
	nr, nc, cells, err := DecodeXlTable(raw)
	if err != nil {
		t.Fatal(err)
	}
	if nr != 1 || nc != 3 {
		t.Fatalf("dims got %dx%d", nr, nc)
	}
	if len(cells) != 3 || cells[0] != "East" || cells[1] != "West" || cells[2] != "North" {
		t.Fatalf("cells %#v", cells)
	}
}

func TestDecodeXlTable_Example2_Blanks(t *testing.T) {
	raw := []byte{
		0x10, 0x00, 0x04, 0x00, 0x02, 0x00, 0x04, 0x00,
		0x06, 0x00, 0x08, 0x00, 0x02, 0x00, 0x03, 0x00, 0x04, 0x00, 0x05, 0x00,
		0x05, 0x00, 0x02, 0x00, 0x02, 0x00,
		0x06, 0x00, 0x04, 0x00, 0x06, 0x00, 0x08, 0x00,
	}
	nr, nc, cells, err := DecodeXlTable(raw)
	if err != nil {
		t.Fatal(err)
	}
	if nr != 2 || nc != 4 {
		t.Fatalf("dims got %dx%d", nr, nc)
	}
	if len(cells) != 8 {
		t.Fatalf("len %d", len(cells))
	}
	want := []string{"2", "3", "4", "5", "", "", "6", "8"}
	for i := range want {
		if cells[i] != want[i] {
			t.Fatalf("i=%d got %q want %q", i, cells[i], want[i])
		}
	}
}

func TestParseItemRange(t *testing.T) {
	r1, c1, r2, c2, ok := ParseItemRange("R1C1_R35C9")
	if !ok || r1 != 1 || c1 != 1 || r2 != 35 || c2 != 9 {
		t.Fatalf("underscore range: %d,%d-%d,%d ok=%v", r1, c1, r2, c2, ok)
	}
	r1, c1, r2, c2, ok = ParseItemRange("R29C1:R29C9")
	if !ok || r1 != 29 || c1 != 1 || r2 != 29 || c2 != 9 {
		t.Fatalf("colon range")
	}
	if _, _, _, _, ok := ParseItemRange("params"); ok {
		t.Fatal("params should not parse as range")
	}
}

func TestSheetNameFromTopic(t *testing.T) {
	if s := SheetNameFromTopic("[data]params"); s != "params" {
		t.Fatalf("got %q", s)
	}
	if s := SheetNameFromTopic("data [mylist]"); s != "mylist" {
		t.Fatalf("got %q", s)
	}
}
