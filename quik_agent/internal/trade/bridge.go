// Package trade is the Phase 2 order/execution layer of the QUIK agent. It is
// strictly HUMAN-INITIATED: the agent NEVER places or cancels an order without an
// explicit, confirmed command from STL, and the master flag quik_trading_enabled
// defaults to false (all order commands rejected when off). There is no strategy or
// signal generation here — only placement and maker-working of a human-decided order.
//
// Phase 1 (read-only market data) is untouched. This package adds:
//   - bridge.go    a localhost TCP server speaking newline-JSON to the QUIK Lua script
//   - limits.go    hard limits enforced BEFORE anything reaches Lua/QUIK
//   - manager.go   the order manager (PlaceOrder/CancelOrder/KillSwitch) + STL events
//   - execution.go the 1b maker-working loop (passive, never crossing)
package trade

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// ---- wire types (newline-delimited JSON, one object per line) ----
// These mirror the protocol in quik_agent/PHASE2.md. The agent is the TCP SERVER on
// 127.0.0.1:<trade_bridge_port>; the Lua script connects as a client and reconnects
// on drop.

// placeCmd is agent -> Lua: a NEW_ORDER limit transaction request.
type placeCmd struct {
	Cmd      string `json:"cmd"`      // "place"
	TransID  int64  `json:"trans_id"` // agent-assigned correlation id
	ClientID string `json:"client_id"`
	Class    string `json:"class"`   // CLASSCODE, e.g. SPBFUT
	Sec      string `json:"sec"`     // SECCODE, e.g. RIU6
	Op       string `json:"op"`      // "B" | "S"
	Price    string `json:"price"`   // limit price as string (QUIK transaction field)
	Qty      int64  `json:"qty"`     // contracts
	Type     string `json:"type"`    // "L" (limit)
	Account  string `json:"account"` // trade account (from keymaster, never hardcoded)
}

// cancelCmd is agent -> Lua: a KILL_ORDER transaction request.
type cancelCmd struct {
	Cmd      string `json:"cmd"` // "cancel"
	TransID  int64  `json:"trans_id"`
	OrderNum string `json:"order_num"`
	Class    string `json:"class"`
	Sec      string `json:"sec"`
}

// moveCmd is agent -> Lua: a MOVE_ORDERS transaction request (native atomic move).
// It re-prices (and optionally re-sizes) a resting order in ONE QUIK transaction — no
// cancel+place window. Qty 0 = keep the current quantity. The resulting OnOrder/
// OnTransReply may carry a NEW order_num (QUIK assigns a fresh key on a move), which the
// Lua relays back as an `order` event so the manager can re-key the working order.
type moveCmd struct {
	Cmd      string `json:"cmd"` // "move"
	TransID  int64  `json:"trans_id"`
	OrderNum string `json:"order_num"` // the resting order's current QUIK key
	Class    string `json:"class"`
	Sec      string `json:"sec"`
	Price    string `json:"price"` // new limit price (string, QUIK transaction field)
	Qty      int64  `json:"qty"`   // new quantity; 0 = keep current
}

// luaEvent is Lua -> agent: any of trans_reply / order / trade. A single struct with
// the union of fields keeps decoding to one json.Unmarshal per line; the Event field
// selects which fields are meaningful.
type luaEvent struct {
	Event      string `json:"event"`       // "trans_reply" | "order" | "trade"
	TransID    int64  `json:"trans_id"`    // trans_reply, order
	ResultCode int32  `json:"result_code"` // trans_reply
	OrderNum   string `json:"order_num"`   // order, trade, trans_reply
	State      string `json:"state"`       // order: active|filled|cancelled|rejected
	Balance    int64  `json:"balance"`     // order: unfilled remainder
	Qty        int64  `json:"qty"`         // order: original qty; trade: trade qty
	Price      string `json:"price"`       // order/trade price
	TS         int64  `json:"ts"`          // trade timestamp (epoch, source-defined)
	Text       string `json:"text"`        // trans_reply/order text
}

// BridgeHandler receives decoded Lua events. The order manager implements it. Calls
// happen on the bridge's reader goroutine; implementations must be quick / non-blocking
// or hand off to their own goroutine.
type BridgeHandler interface {
	OnTransReply(ev TransReplyEvent)
	OnOrder(ev OrderEvent)
	OnTrade(ev TradeEvent)
}

// TransReplyEvent is a QUIK OnTransReply, correlated by trans_id.
type TransReplyEvent struct {
	TransID    int64
	ResultCode int32
	OrderNum   string
	Text       string
}

// OrderEvent is a QUIK OnOrder lifecycle update.
type OrderEvent struct {
	OrderNum string
	TransID  int64
	State    string // active|filled|cancelled|rejected
	Balance  int64  // unfilled remainder
	Qty      int64  // original quantity
	Price    string
	Text     string
}

// TradeEvent is a QUIK OnTrade fill.
type TradeEvent struct {
	OrderNum string
	Qty      int64
	Price    string
	TS       int64
}

// Bridge is the localhost TCP server toward the Lua script. It accepts one Lua
// client at a time (the newest connection wins; an older one is closed), is tolerant
// of disconnect/reconnect, and exposes Place/Cancel to push commands out. Inbound
// lines are decoded and dispatched to the handler.
type Bridge struct {
	addr    string
	handler BridgeHandler
	logf    func(string, ...any)

	ln net.Listener

	mu   sync.Mutex
	conn net.Conn // current Lua client, or nil when none connected

	// File-queue fallback (used when QUIK has no LuaSocket). When queueDir != "",
	// the bridge appends commands to <dir>/cmd.jsonl and tails <dir>/evt.jsonl by
	// byte offset instead of using TCP. Same newline-JSON schema. See PHASE2.md.
	queueDir string
	cmdMu    sync.Mutex // serializes appends to cmd.jsonl
	evtOff   int64      // bytes consumed from evt.jsonl

	transSeq atomic.Int64
}

// SetQueueDir switches the bridge to the file-queue transport (no TCP). Call before
// Run. dir must match the Lua script's CONFIG.QUEUE_DIR.
func (b *Bridge) SetQueueDir(dir string) { b.queueDir = dir }

// NewBridge builds a bridge bound to 127.0.0.1:port. handler may be set later via
// SetHandler (the manager wires itself in after construction). logf may be nil.
func NewBridge(port int, handler BridgeHandler, logf func(string, ...any)) *Bridge {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	return &Bridge{
		addr:    fmt.Sprintf("127.0.0.1:%d", port),
		handler: handler,
		logf:    logf,
	}
}

// SetHandler sets/replaces the event handler. Safe to call before Run.
func (b *Bridge) SetHandler(h BridgeHandler) {
	b.mu.Lock()
	b.handler = h
	b.mu.Unlock()
}

// NextTransID assigns a fresh, monotonically increasing TRANS_ID. QUIK TRANS_ID is a
// positive integer; the manager uses it to correlate place/cancel with trans_reply
// and order events.
func (b *Bridge) NextTransID() int64 { return b.transSeq.Add(1) }

// Connected reports whether a Lua client is currently attached. In file-queue mode
// the transport is the filesystem, so it is always "connected".
func (b *Bridge) Connected() bool {
	if b.queueDir != "" {
		return true
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.conn != nil
}

// Run binds the listener and accepts Lua connections until ctx is cancelled. It only
// ever serves loopback. A new connection replaces any previous one. Run blocks; start
// it in a goroutine.
func (b *Bridge) Run(ctx context.Context) error {
	if b.queueDir != "" {
		return b.runFileQueue(ctx)
	}
	lc := net.ListenConfig{}
	ln, err := lc.Listen(ctx, "tcp", b.addr)
	if err != nil {
		return fmt.Errorf("trade bridge listen %s: %w", b.addr, err)
	}
	b.ln = ln
	b.logf("trade bridge listening on %s (Lua connects as client)", b.addr)

	go func() {
		<-ctx.Done()
		_ = ln.Close()
		b.mu.Lock()
		if b.conn != nil {
			_ = b.conn.Close()
		}
		b.mu.Unlock()
	}()

	for {
		conn, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			b.logf("trade bridge accept error: %v", err)
			// Transient accept error: small pause, then keep serving.
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(200 * time.Millisecond):
			}
			continue
		}
		b.adopt(conn)
		go b.readLoop(ctx, conn)
	}
}

// adopt installs conn as the current Lua client, closing any previous one.
func (b *Bridge) adopt(conn net.Conn) {
	b.mu.Lock()
	old := b.conn
	b.conn = conn
	b.mu.Unlock()
	if old != nil {
		_ = old.Close()
	}
	b.logf("trade bridge: Lua client connected from %s", conn.RemoteAddr())
}

// readLoop decodes newline-JSON events from one Lua connection and dispatches them.
// It returns when the connection drops (EOF/error) or ctx is cancelled.
func (b *Bridge) readLoop(ctx context.Context, conn net.Conn) {
	sc := bufio.NewScanner(conn)
	// QUIK rows / texts are small; allow up to 1 MiB per line defensively.
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		if ctx.Err() != nil {
			return
		}
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var ev luaEvent
		if err := json.Unmarshal(line, &ev); err != nil {
			b.logf("trade bridge: bad line: %v", err)
			continue
		}
		b.dispatch(ev)
	}
	// Connection ended; clear it if it is still the current one.
	b.mu.Lock()
	if b.conn == conn {
		b.conn = nil
	}
	b.mu.Unlock()
	_ = conn.Close()
	b.logf("trade bridge: Lua client disconnected")
}

func (b *Bridge) dispatch(ev luaEvent) {
	b.mu.Lock()
	h := b.handler
	b.mu.Unlock()
	if h == nil {
		return
	}
	switch ev.Event {
	case "trans_reply":
		h.OnTransReply(TransReplyEvent{
			TransID:    ev.TransID,
			ResultCode: ev.ResultCode,
			OrderNum:   ev.OrderNum,
			Text:       ev.Text,
		})
	case "order":
		h.OnOrder(OrderEvent{
			OrderNum: ev.OrderNum,
			TransID:  ev.TransID,
			State:    ev.State,
			Balance:  ev.Balance,
			Qty:      ev.Qty,
			Price:    ev.Price,
			Text:     ev.Text,
		})
	case "trade":
		h.OnTrade(TradeEvent{
			OrderNum: ev.OrderNum,
			Qty:      ev.Qty,
			Price:    ev.Price,
			TS:       ev.TS,
		})
	default:
		b.logf("trade bridge: unknown event %q", ev.Event)
	}
}

// errNoLua is returned by send when no Lua client is connected.
var errNoLua = fmt.Errorf("trade bridge: no Lua client connected")

// send writes one JSON object + newline to the current Lua client. It fails fast if
// no client is attached so the manager can reject the command instead of blocking.
func (b *Bridge) send(v any) error {
	if b.queueDir != "" {
		return b.appendCmd(v)
	}
	b.mu.Lock()
	conn := b.conn
	b.mu.Unlock()
	if conn == nil {
		return errNoLua
	}
	buf, err := json.Marshal(v)
	if err != nil {
		return err
	}
	buf = append(buf, '\n')
	_ = conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if _, err := conn.Write(buf); err != nil {
		// Drop a wedged connection so the next reconnect can take over.
		b.mu.Lock()
		if b.conn == conn {
			b.conn = nil
		}
		b.mu.Unlock()
		_ = conn.Close()
		return err
	}
	return nil
}

// Place sends a NEW_ORDER limit transaction to Lua. The caller (manager) has already
// assigned the trans_id and passed every hard limit.
func (b *Bridge) Place(p placeCmd) error {
	p.Cmd = "place"
	p.Type = "L"
	return b.send(p)
}

// Cancel sends a KILL_ORDER transaction to Lua.
func (b *Bridge) Cancel(c cancelCmd) error {
	c.Cmd = "cancel"
	return b.send(c)
}

// Move sends a MOVE_ORDERS transaction to Lua (native atomic re-quote). The caller
// (manager) has already assigned the trans_id, resolved the resting order_num, and
// passed every hard limit (collar on the new price, qty within the per-order cap).
func (b *Bridge) Move(m moveCmd) error {
	m.Cmd = "move"
	return b.send(m)
}
