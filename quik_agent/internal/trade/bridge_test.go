package trade

import (
	"encoding/json"
	"testing"
)

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
