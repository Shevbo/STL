package trade

import (
	"bufio"
	"encoding/json"
	"net"
	"testing"
)

// TestOwnerTag drives the client_id -> QUIK-order-COMMENT tag mapping used by
// manager.go's place-cmd build: a robot's ID for "rr:<robotID>:<seq>", "recon" for
// an align order ("recon:<planID>:<n>"), else the raw client_id verbatim.
func TestOwnerTag(t *testing.T) {
	cases := map[string]string{
		"rr:agent-fvg-RIU6-v2:1": "agent-fvg-RIU6-v2",
		"recon:ab8fffa61d4a:0":   "recon",
		"human-7":                "human-7",
		"":                       "",
	}
	for in, want := range cases {
		if got := ownerTag(in); got != want {
			t.Fatalf("ownerTag(%q)=%q want %q", in, got, want)
		}
	}
}

// TestSendPing_EncodesCmdAndT0 drives the REAL Bridge.send() plumbing (not the
// bridgeAPI interface the manager sees) over a net.Pipe, mirroring how the Lua
// script would read the line off the loopback TCP connection.
func TestSendPing_EncodesCmdAndT0(t *testing.T) {
	b := NewBridge(0, nil, nil)
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	b.conn = client

	lineCh := make(chan string, 1)
	go func() {
		sc := bufio.NewScanner(server)
		if sc.Scan() {
			lineCh <- sc.Text()
		}
	}()

	if err := b.SendPing(123456); err != nil {
		t.Fatalf("SendPing: %v", err)
	}

	var got map[string]any
	if err := json.Unmarshal([]byte(<-lineCh), &got); err != nil {
		t.Fatalf("bad json line: %v", err)
	}
	if got["cmd"] != "ping" {
		t.Errorf("cmd = %v, want %q", got["cmd"], "ping")
	}
	if got["t0"] != float64(123456) {
		t.Errorf("t0 = %v, want 123456", got["t0"])
	}
}

func TestSendPing_NoLuaClientReturnsError(t *testing.T) {
	b := NewBridge(0, nil, nil)
	if err := b.SendPing(1); err == nil {
		t.Fatal("SendPing without a connected Lua client must return an error")
	}
}

func TestDispatchAccountEvents(t *testing.T) {
	var got []AccEvent
	b := NewBridge(0, nil, nil)
	b.SetAccSink(func(e AccEvent) { got = append(got, e) })
	for _, line := range []string{
		`{"event":"acc_pos","rows":[["RIU6",2,89100.0]]}`,
		`{"event":"acc_ord","rows":[["123","RIU6",1,89000,1,1]]}`,
		`{"event":"acc_trd","rows":[["t1","123","RIU6",89050,1,1751700000000]]}`,
		`{"event":"pong","t0":100,"ts":200,"server_time":"12:00:01"}`,
	} {
		var ev luaEvent
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			t.Fatal(err)
		}
		b.dispatch(ev)
	}
	if len(got) != 4 || got[0].Kind != "pos" || got[3].ServerTime != "12:00:01" {
		t.Fatalf("got %+v", got)
	}
}

// A flat account emits acc_pos/acc_ord with an EMPTY rows array (the Lua encoder
// serialises {} as []). That frame must decode cleanly and dispatch Kind=pos with
// zero rows, so main.go calls SetPositions([]) and recon reads OK-empty (not STALE).
// A JSON object {} here would fail json.Unmarshal into [][]any and drop the frame.
func TestDispatchAccountEventsEmptyRows(t *testing.T) {
	var got []AccEvent
	b := NewBridge(0, nil, nil)
	b.SetAccSink(func(e AccEvent) { got = append(got, e) })
	var ev luaEvent
	if err := json.Unmarshal([]byte(`{"event":"acc_pos","rows":[]}`), &ev); err != nil {
		t.Fatalf("empty-rows acc_pos must unmarshal, got %v", err)
	}
	b.dispatch(ev)
	if len(got) != 1 || got[0].Kind != "pos" || len(got[0].Rows) != 0 {
		t.Fatalf("got %+v, want one pos event with zero rows", got)
	}
}

// The wire contract the Lua encoder must honor: 13-digit epoch-ms integers
// (pong t0 / last_trade_ts_ms) survive decode as full int64, not truncated. This
// guards the %.0f Lua encoder fix against a regression back to %d (which zeros
// large integers on QUIK's 32-bit Lua and collapsed rtt_ms to an epoch value).
func TestDispatchPongLargeEpochSurvives(t *testing.T) {
	var got []AccEvent
	b := NewBridge(0, nil, nil)
	b.SetAccSink(func(e AccEvent) { got = append(got, e) })
	var ev luaEvent
	line := `{"event":"pong","t0":1783340089079,"ts":1783340089079,"server_time":"12:00:01","last_trade_ts_ms":1783340089000}`
	if err := json.Unmarshal([]byte(line), &ev); err != nil {
		t.Fatal(err)
	}
	b.dispatch(ev)
	if len(got) != 1 || got[0].T0 != 1783340089079 || got[0].LastTradeTsMs != 1783340089000 {
		t.Fatalf("pong = %+v, want full int64 t0/last_trade_ts_ms", got)
	}
}

// TestDispatchPongDecodesLastTradeTsMs: the QLua pong's last_trade_ts_ms field (freshest
// OnAllTrade exchange timestamp) must decode through luaEvent into AccEvent verbatim —
// this is what accounts.Store.SetPong uses to compute ExchangeLagMs (see accounts_test.go
// TestStoreExchangeLagFromPong).
func TestDispatchPongDecodesLastTradeTsMs(t *testing.T) {
	var got []AccEvent
	b := NewBridge(0, nil, nil)
	b.SetAccSink(func(e AccEvent) { got = append(got, e) })

	var ev luaEvent
	line := `{"event":"pong","t0":100,"ts":200,"server_time":"12:00:01","last_trade_ts_ms":1751700000000}`
	if err := json.Unmarshal([]byte(line), &ev); err != nil {
		t.Fatal(err)
	}
	b.dispatch(ev)
	if len(got) != 1 || got[0].LastTradeTsMs != 1751700000000 {
		t.Fatalf("pong event = %+v, want LastTradeTsMs=1751700000000", got)
	}
}

func TestDispatchAccountEventsNilSink(t *testing.T) {
	// Without SetAccSink, acc_*/pong events must be dropped silently (no panic).
	b := NewBridge(0, nil, nil)
	var ev luaEvent
	if err := json.Unmarshal([]byte(`{"event":"acc_pos","rows":[["RIU6",2,89100.0]]}`), &ev); err != nil {
		t.Fatal(err)
	}
	b.dispatch(ev)
}

func TestDispatchAccountEventsFieldMapping(t *testing.T) {
	var got []AccEvent
	b := NewBridge(0, nil, nil)
	b.SetAccSink(func(e AccEvent) { got = append(got, e) })

	var pong luaEvent
	if err := json.Unmarshal([]byte(`{"event":"pong","t0":100,"ts":200,"server_time":"12:00:01"}`), &pong); err != nil {
		t.Fatal(err)
	}
	b.dispatch(pong)
	if len(got) != 1 {
		t.Fatalf("got %d events, want 1", len(got))
	}
	if got[0].Kind != "pong" || got[0].T0 != 100 || got[0].TS != 200 || got[0].ServerTime != "12:00:01" {
		t.Fatalf("pong event = %+v", got[0])
	}
	if got[0].LastTradeTsMs != 0 {
		t.Fatalf("LastTradeTsMs = %d, want 0 (field absent in this line)", got[0].LastTradeTsMs)
	}

	got = nil
	var ord luaEvent
	if err := json.Unmarshal([]byte(`{"event":"acc_ord","rows":[["123","RIU6",1,89000,1,1]]}`), &ord); err != nil {
		t.Fatal(err)
	}
	b.dispatch(ord)
	if len(got) != 1 || got[0].Kind != "ord" || len(got[0].Rows) != 1 {
		t.Fatalf("ord event = %+v", got)
	}
}
