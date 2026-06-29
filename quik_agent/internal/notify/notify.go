// Package notify is a thin LOCAL alert fallback. The agent's PRIMARY alert path is
// the gRPC Alert frame to STL (STL fans CRITICAL alerts to Telegram + Phase-2 SMS).
// This package exists only for the case where the gRPC link ITSELF is DOWN, so a
// link outage can still be signalled out-of-band.
//
// Secrets policy: the Telegram bot token and chat id are read from ENV VAR NAMES
// only. No value is hardcoded, logged, or stored on disk. If either env var is
// unset the notifier is a no-op (it never errors the agent).
//
// Phase 1 is READ-ONLY and this package only SENDS a text message; it issues no
// order and reads no market data. CRITICAL severity is marked for the Phase-2 SMS
// dub here too, but no SMS gateway is contacted in Phase 1.
package notify

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// Env var NAMES (documented in README). Values are never embedded in the binary.
const (
	// EnvTelegramToken names the env var holding the Telegram bot token.
	EnvTelegramToken = "SHECTORY_AGENT_TG_BOT_TOKEN"
	// EnvTelegramChatID names the env var holding the target chat id.
	EnvTelegramChatID = "SHECTORY_AGENT_TG_CHAT_ID"
)

// Severity is a local mirror of the alert severity, decoupled from the proto so
// callers in any package can use it without importing the generated stubs. CRITICAL
// is the level that Phase 2 will also dub to SMS.
type Severity int

const (
	SeverityInfo Severity = iota
	SeverityWarn
	SeverityCritical
)

func (s Severity) String() string {
	switch s {
	case SeverityCritical:
		return "CRITICAL"
	case SeverityWarn:
		return "WARN"
	default:
		return "INFO"
	}
}

// Notifier is the local fallback sink interface. Send must be safe to call when
// the sink is unconfigured (it returns nil and does nothing).
type Notifier interface {
	// Send delivers an alert out-of-band. code is the machine code (e.g. LINK_DOWN);
	// smsDub marks a CRITICAL alert that Phase 2 will also SMS-dub.
	Send(ctx context.Context, sev Severity, code, message string, smsDub bool) error
	// Enabled reports whether the sink is configured (env vars present).
	Enabled() bool
}

// nopNotifier is used when no sink is configured.
type nopNotifier struct{}

func (nopNotifier) Send(context.Context, Severity, string, string, bool) error { return nil }
func (nopNotifier) Enabled() bool                                              { return false }

// Nop returns a notifier that does nothing. Useful as a safe default.
func Nop() Notifier { return nopNotifier{} }

// telegramNotifier posts to the Telegram Bot API sendMessage endpoint.
type telegramNotifier struct {
	token  string // read from env at construction; never logged
	chatID string
	client *http.Client
	host   string // override for tests; defaults to api.telegram.org
}

// FromEnv builds a Notifier from the documented env var NAMES. If either env var
// is empty it returns a Nop notifier (the agent runs fine without a local sink).
func FromEnv() Notifier {
	token := strings.TrimSpace(os.Getenv(EnvTelegramToken))
	chatID := strings.TrimSpace(os.Getenv(EnvTelegramChatID))
	if token == "" || chatID == "" {
		return Nop()
	}
	return &telegramNotifier{
		token:  token,
		chatID: chatID,
		client: &http.Client{Timeout: 10 * time.Second},
		host:   "https://api.telegram.org",
	}
}

func (t *telegramNotifier) Enabled() bool { return t.token != "" && t.chatID != "" }

// Send posts the alert text to Telegram. smsDub is recorded in the message prefix
// (Phase 2 wires the actual SMS gateway); no SMS is sent here.
func (t *telegramNotifier) Send(ctx context.Context, sev Severity, code, message string, smsDub bool) error {
	if !t.Enabled() {
		return nil
	}
	prefix := "[" + sev.String() + "]"
	if smsDub {
		prefix += "[SMS]"
	}
	text := fmt.Sprintf("%s %s: %s (local fallback — gRPC link down)", prefix, code, message)

	body, err := json.Marshal(map[string]any{
		"chat_id": t.chatID,
		"text":    text,
	})
	if err != nil {
		return err
	}

	url := fmt.Sprintf("%s/bot%s/sendMessage", t.host, t.token)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := t.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("telegram sendMessage: status %d", resp.StatusCode)
	}
	return nil
}
