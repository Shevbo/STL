package trade

import (
	"errors"
	"fmt"
	"strconv"
	"strings"

	quikv1 "shectory/quik_agent/internal/pb"
)

// Native QUIK stop orders (docs/design/execution-module.md, stage 1).
//
// Stage 1 is discovery on GZ: the kind-specific QUIK fields (STOP_ORDER_KIND, STOPPRICE,
// OFFSET, ...) are not verified on our terminal, so they travel from STL as a raw map and
// the QUIK rows come back verbatim. What IS fixed here is the safety frame: a stop order
// is placed only through the same gate as a limit order, and the fields that decide
// WHERE and HOW MUCH (account, instrument, side, quantity) are stamped by the agent and
// can never be smuggled in through the map.

// reservedStopFields are stamped by the agent (or by Lua for TRANS_ID/CLIENT_CODE).
var reservedStopFields = map[string]bool{
	"ACTION": true, "TRANS_ID": true, "CLASSCODE": true, "SECCODE": true,
	"OPERATION": true, "QUANTITY": true, "ACCOUNT": true, "CLIENT_CODE": true,
}

// PlaceStopOrder sends a NEW_STOP_ORDER. Rejections go back as a TransReply carrying
// the client_id and result_code -1, the same channel QUIK's own refusal arrives on.
func (m *Manager) PlaceStopOrder(req *quikv1.PlaceStopOrder) { _ = m.PlaceStopOrderErr(req) }

// PlaceStopOrderErr is PlaceStopOrder returning the rejection reason.
func (m *Manager) PlaceStopOrderErr(req *quikv1.PlaceStopOrder) error {
	if req == nil {
		return errors.New("nil PlaceStopOrder")
	}
	reject := func(reason string) error {
		m.logf("trade: PlaceStopOrder REJECTED (client=%q code=%q): %s", req.GetClientId(), req.GetCode(), reason)
		m.emitStopReject(req.GetClientId(), reason)
		return errors.New(reason)
	}
	for k := range req.GetFields() {
		if reservedStopFields[strings.ToUpper(strings.TrimSpace(k))] {
			return reject("field " + k + " is stamped by the agent")
		}
	}
	op := ""
	switch req.GetSide() {
	case quikv1.Side_SIDE_BUY:
		op = "B"
	case quikv1.Side_SIDE_SELL:
		op = "S"
	default:
		return reject("side must be BUY or SELL")
	}
	m.mu.Lock()
	blocked := m.blocked
	m.mu.Unlock()
	if blocked {
		return reject(string(ReasonBlocked))
	}
	// A stop order rests on the broker's server, not in the book: it does not occupy the
	// working budget until it activates, and its trigger price is by design far from the
	// market. Price 1 satisfies the non-positive-price check; the collar applies to the
	// child order QUIK creates, which the manager does not price.
	if ok, reason := m.guard.CheckPlace(PlaceCheck{
		Code: req.GetCode(), Price: 1, Quantity: req.GetQuantity(),
	}); !ok {
		return reject(string(reason))
	}
	if ok, reason := m.guard.CommitPlace(); !ok {
		return reject(string(reason))
	}

	fields := map[string]string{
		"ACTION":    "NEW_STOP_ORDER",
		"CLASSCODE": m.cfg.ClassCode,
		"SECCODE":   req.GetCode(),
		"OPERATION": op,
		"QUANTITY":  strconv.FormatInt(req.GetQuantity(), 10),
	}
	if m.cfg.Account != "" {
		fields["ACCOUNT"] = m.cfg.Account
	}
	for k, v := range req.GetFields() {
		fields[strings.ToUpper(strings.TrimSpace(k))] = v
	}
	return m.sendStopTx(req.GetClientId(), fields)
}

// KillStopOrder sends a KILL_STOP_ORDER. Removing a stop order never creates exposure,
// so, like CancelOrder, it is allowed with the master flag off.
func (m *Manager) KillStopOrder(req *quikv1.KillStopOrder) { _ = m.KillStopOrderErr(req) }

// KillStopOrderErr is KillStopOrder returning the rejection reason.
func (m *Manager) KillStopOrderErr(req *quikv1.KillStopOrder) error {
	if req == nil {
		return errors.New("nil KillStopOrder")
	}
	if strings.TrimSpace(req.GetStopOrderNum()) == "" || strings.TrimSpace(req.GetCode()) == "" {
		reason := "stop_order_num and code are required"
		m.emitStopReject(req.GetClientId(), reason)
		return errors.New(reason)
	}
	return m.sendStopTx(req.GetClientId(), map[string]string{
		"ACTION":         "KILL_STOP_ORDER",
		"CLASSCODE":      m.cfg.ClassCode,
		"SECCODE":        req.GetCode(),
		"STOP_ORDER_KEY": req.GetStopOrderNum(),
	})
}

func (m *Manager) sendStopTx(clientID string, fields map[string]string) error {
	transID := m.bridge.NextTransID()
	m.mu.Lock()
	m.stopTrans[transID] = clientID
	m.mu.Unlock()
	m.logf("trade: stop_tx trans=%d client=%q %v", transID, clientID, fields)
	if err := m.bridge.StopTx(stopTxCmd{TransID: transID, Comment: ownerTag(clientID), Fields: fields}); err != nil {
		reason := fmt.Sprintf("bridge: %v", err)
		m.emitStopReject(clientID, reason)
		return errors.New(reason)
	}
	return nil
}

func (m *Manager) emitStopReject(clientID, reason string) {
	if m.emit == nil {
		return
	}
	_ = m.emit.EmitTransReply(&quikv1.TransReply{
		ClientId: clientID, ResultCode: -1, Text: reason, TsUnixMs: m.nowMs(),
	})
}

// OnStopEvent relays a QUIK stop-order event or stop_orders snapshot to STL verbatim.
func (m *Manager) OnStopEvent(ev StopEvent) {
	if m.emit == nil {
		return
	}
	rep := &quikv1.StopOrderReport{IsTable: ev.IsTable, ReceivedAtUnixMs: m.nowMs()}
	if ev.IsTable {
		for _, r := range ev.Maps {
			rep.Rows = append(rep.Rows, &quikv1.StopOrderRow{Fields: stringifyFields(r)})
		}
	} else {
		rep.Rows = []*quikv1.StopOrderRow{{Fields: stringifyFields(ev.Fields)}}
	}
	_ = m.emit.EmitStopOrderReport(rep)
}

// stringifyFields renders JSON-decoded QUIK values as text. Integers came as strings
// from Lua already (exact); floats print without exponent so a price reads as QUIK shows.
func stringifyFields(in map[string]any) map[string]string {
	out := make(map[string]string, len(in))
	for k, v := range in {
		switch x := v.(type) {
		case string:
			out[k] = x
		case float64:
			out[k] = strconv.FormatFloat(x, 'f', -1, 64)
		case bool:
			out[k] = strconv.FormatBool(x)
		case nil:
			out[k] = ""
		default:
			out[k] = fmt.Sprint(x)
		}
	}
	return out
}
