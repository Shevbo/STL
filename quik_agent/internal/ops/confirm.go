package ops

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"sync"
)

// ConfirmTTLMs is how long a human approval stays valid. Five minutes, because an
// approval given half an hour ago belongs to a different market situation: the
// position may have closed, the session may have ended, the tape may have died.
const ConfirmTTLMs int64 = 5 * 60 * 1000

// Binding is what a confirmation is tied to. A token that approved restarting QUIK
// must not reboot the machine, and a token approving "open the deals table" must not
// open a different one — so the binding covers the operation AND its arguments.
func Binding(op string, args map[string]string) string {
	keys := make([]string, 0, len(args))
	for k := range args {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	b.WriteString(op)
	for _, k := range keys {
		b.WriteString("\x1f")
		b.WriteString(k)
		b.WriteString("=")
		b.WriteString(args[k])
	}
	sum := sha256.Sum256([]byte(b.String()))
	return hex.EncodeToString(sum[:])
}

// Approval is one operator confirmation, as the agent remembers it.
type Approval struct {
	ConfirmID string
	Binding   string
	IssuedMs  int64
}

// Confirmations tracks which approvals were issued and which were already spent.
// Spent ids are kept for the rest of the run: replaying a used token is exactly the
// attack this guards against, and forgetting it after a minute would reopen it.
type Confirmations struct {
	mu       sync.Mutex
	pending  map[string]Approval
	consumed map[string]struct{}
}

func NewConfirmations() *Confirmations {
	return &Confirmations{pending: map[string]Approval{}, consumed: map[string]struct{}{}}
}

// Grant records an approval the operator gave (relayed by STL).
func (c *Confirmations) Grant(a Approval) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.pending[a.ConfirmID] = a
}

// Use validates and SPENDS a confirmation for this exact operation and arguments.
// Every refusal names its reason: a silent "no" during an incident is as bad as a
// wrong "yes".
func (c *Confirmations) Use(confirmID, op string, args map[string]string, nowMs int64) error {
	if confirmID == "" {
		return fmt.Errorf("операция %s меняет состояние и требует подтверждения оператора", op)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, spent := c.consumed[confirmID]; spent {
		return fmt.Errorf("подтверждение уже использовано: одно подтверждение — одна операция")
	}
	a, ok := c.pending[confirmID]
	if !ok {
		return fmt.Errorf("подтверждение неизвестно агенту")
	}
	if nowMs-a.IssuedMs > ConfirmTTLMs {
		delete(c.pending, confirmID)
		return fmt.Errorf("подтверждение просрочено (выдано %d с назад, срок %d с)",
			(nowMs-a.IssuedMs)/1000, ConfirmTTLMs/1000)
	}
	if a.Binding != Binding(op, args) {
		return fmt.Errorf("подтверждение выдано на другую операцию или другие аргументы")
	}
	delete(c.pending, confirmID)
	c.consumed[confirmID] = struct{}{}
	return nil
}
