package trade

import (
	"bufio"
	"encoding/json"
	"net"
	"testing"
)

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
