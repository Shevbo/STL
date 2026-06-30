package trade

import (
	"encoding/json"
	"testing"
)

func TestIsTransReject(t *testing.T) {
	// QUIK progress statuses that are NOT rejections.
	for _, ok := range []int32{0, 1, 3} {
		if isTransReject(ok) {
			t.Fatalf("status %d must NOT be a rejection (it is sent/received/executed)", ok)
		}
	}
	// Real rejections: transmit error, not-executed, failed checks, and Lua's own -1.
	for _, bad := range []int32{-1, 2, 4, 5, 6, 13} {
		if !isTransReject(bad) {
			t.Fatalf("status %d must be a rejection", bad)
		}
	}
}

func TestToUTF8DecodesWindows1251(t *testing.T) {
	// "Заявка" in Windows-1251 (what QUIK hands QLua on a Russian box).
	cp1251 := []byte{0xC7, 0xE0, 0xFF, 0xE2, 0xEA, 0xE0}
	got := string(toUTF8(cp1251))
	if got != "Заявка" {
		t.Fatalf("toUTF8 Windows-1251 = %q, want %q", got, "Заявка")
	}

	// A full evt line with CP1251 text must json-decode to correct Cyrillic.
	line := append([]byte(`{"event":"trans_reply","trans_id":1,"result_code":3,"text":"`), cp1251...)
	line = append(line, []byte(` 18960 OK"}`)...)
	var ev luaEvent
	if err := json.Unmarshal(toUTF8(line), &ev); err != nil {
		t.Fatalf("unmarshal after toUTF8 failed: %v", err)
	}
	if ev.Text != "Заявка 18960 OK" {
		t.Fatalf("decoded text = %q, want %q", ev.Text, "Заявка 18960 OK")
	}

	// Already-valid UTF-8 (ASCII) passes through untouched.
	ascii := []byte(`{"event":"order","order_num":"5"}`)
	if string(toUTF8(ascii)) != string(ascii) {
		t.Fatal("valid UTF-8 line must pass through unchanged")
	}
}
