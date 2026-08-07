package main

// Link: the credential store and the loopback proxy the panel talks to.
//
// The panel HTML never sees a token. It calls same-origin paths on
// http://127.0.0.1:<random>, and this proxy attaches the Bearer header and
// forwards to STL. That also sidesteps CORS and cookies entirely.
//
// The proxy binds loopback only. A hostile process running AS THIS USER could
// call it — but that same process can already read the DPAPI blob, so the proxy
// widens nothing. /open is the one route that could be abused as a generic
// "launch anything" gadget, so it is restricted to the configured STL origin.

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const defaultSTL = "https://stl.shectory.ru"

type config struct {
	STLURL string `json:"stl_url"`
	// BGAlpha is the warm-paper backdrop opacity, 0..255 (default 128 = 50%).
	// A knob, not a constant: readability depends on the operator's wallpaper
	// and monitor, and no fixed value is right on every desk.
	BGAlpha int `json:"bg_alpha"`
	Width   int `json:"width"`
	// Куда оператор перетащил панель. Placed=false — панель живёт в правом
	// нижнем углу, как раньше, и следует за сменой разрешения и панели задач.
	Placed bool `json:"placed"`
	PosX   int  `json:"pos_x"`
	PosY   int  `json:"pos_y"`
}

func configDir() string {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		base = os.TempDir()
	}
	return filepath.Join(base, "STLCompanion")
}

func loadConfig() config {
	c := config{STLURL: defaultSTL, BGAlpha: 128, Width: 380}
	if b, err := os.ReadFile(filepath.Join(configDir(), "config.json")); err == nil {
		_ = json.Unmarshal(b, &c)
	}
	if c.STLURL == "" {
		c.STLURL = defaultSTL
	}
	if c.BGAlpha <= 0 || c.BGAlpha > 255 {
		c.BGAlpha = 128
	}
	if c.Width < 260 || c.Width > 900 {
		c.Width = 380
	}
	return c
}

func (c config) save() error {
	if err := os.MkdirAll(configDir(), 0o700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(c, "", " ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(configDir(), "config.json"), b, 0o600)
}

// ---- token storage (DPAPI, bound to the Windows account) ----

func tokenPath() string { return filepath.Join(configDir(), "token.bin") }

func loadToken() string {
	blob, err := os.ReadFile(tokenPath())
	if err != nil {
		return ""
	}
	plain, err := dpapiUnprotect(blob)
	if err != nil {
		// Wrong user or a corrupt file: treat as "not paired" and re-pair.
		return ""
	}
	return strings.TrimSpace(string(plain))
}

func saveToken(tok string) error {
	if err := os.MkdirAll(configDir(), 0o700); err != nil {
		return err
	}
	blob, err := dpapiProtect([]byte(tok))
	if err != nil {
		return err
	}
	return os.WriteFile(tokenPath(), blob, 0o600)
}

// ---- proxy ----

type link struct {
	cfg     config
	token   string
	client  *http.Client
	account string

	onHide     func()
	onQuit     func()
	onHeight   func(int)
	onWidth    func(int)
	onClip     func(string) error
	onMove     func(dx, dy int)
	onMoveDone func()
}

func newLink(cfg config) *link {
	return &link{
		cfg:    cfg,
		token:  loadToken(),
		client: &http.Client{Timeout: 8 * time.Second},
		// whoami — the account this companion claims. STL checks it against the
		// account the pairing code was issued for.
		account: strings.ToUpper(os.Getenv("USERDOMAIN") + `\` + os.Getenv("USERNAME")),
	}
}

func (l *link) paired() bool { return l.token != "" }

// serve starts the loopback listener and returns its base URL.
func (l *link) serve() (string, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return "", err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/", l.handleRoot)
	mux.HandleFunc("/snapshot", l.handleSnapshot)
	mux.HandleFunc("/pair", l.handlePair)
	mux.HandleFunc("/open", l.handleOpen)
	mux.HandleFunc("/hide", func(w http.ResponseWriter, _ *http.Request) {
		if l.onHide != nil {
			l.onHide()
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/quit", func(w http.ResponseWriter, _ *http.Request) {
		if l.onQuit != nil {
			l.onQuit()
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/height", func(w http.ResponseWriter, r *http.Request) {
		if h, err := strconv.Atoi(r.URL.Query().Get("h")); err == nil && l.onHeight != nil {
			l.onHeight(h)
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/width", func(w http.ResponseWriter, r *http.Request) {
		if px, err := strconv.Atoi(r.URL.Query().Get("w")); err == nil && l.onWidth != nil {
			l.onWidth(px)
		}
		w.WriteHeader(http.StatusNoContent)
	})
	// Перетаскивание панели. Страница шлёт ИНКРЕМЕНТНЫЕ дельты в физических
	// пикселях, save=1 на отпускании кнопки закрепляет позицию в config.json.
	// Рамки у окна нет, поэтому системный drag за заголовок недоступен, а
	// WM_NCHITTEST до нас не доходит: мышь принадлежит окну WebView2.
	mux.HandleFunc("/move", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if q.Get("save") != "" {
			if l.onMoveDone != nil {
				l.onMoveDone()
			}
			w.WriteHeader(http.StatusNoContent)
			return
		}
		dx, errX := strconv.Atoi(q.Get("dx"))
		dy, errY := strconv.Atoi(q.Get("dy"))
		if errX != nil || errY != nil {
			http.Error(w, "нужны dx и dy", http.StatusBadRequest)
			return
		}
		if l.onMove != nil {
			l.onMove(dx, dy)
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/clip", func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(io.LimitReader(r.Body, 256<<10))
		if l.onClip != nil {
			if err := l.onClip(string(body)); err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
		}
		w.WriteHeader(http.StatusNoContent)
	})
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	return "http://" + ln.Addr().String() + "/", nil
}

func (l *link) handleRoot(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	if !l.paired() {
		_, _ = io.WriteString(w, pairingHTML(l.account))
		return
	}
	body, err := l.fetch("/companion.html", "")
	if err != nil {
		_, _ = io.WriteString(w, offlineHTML(err))
		return
	}
	_, _ = w.Write(body)
}

func (l *link) handleSnapshot(w http.ResponseWriter, _ *http.Request) {
	body, err := l.fetch("/api/v1/quik/companion/snapshot", l.token)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	if err != nil {
		var he httpError
		code := http.StatusBadGateway
		if errors.As(err, &he) {
			code = he.code
		}
		w.WriteHeader(code)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	_, _ = w.Write(body)
}

// handlePair exchanges the operator's one-time code for a device token and
// stores it DPAPI-encrypted. Runs once per machine, then never again.
func (l *link) handlePair(w http.ResponseWriter, r *http.Request) {
	var in struct{ Code string }
	_ = json.NewDecoder(r.Body).Decode(&in)
	payload, _ := json.Marshal(map[string]string{
		"code": strings.TrimSpace(in.Code), "account": l.account, "machine": hostname(),
	})
	body, err := l.post("/api/v1/quik/companion/pair", payload)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	var out struct{ Token string }
	if err := json.Unmarshal(body, &out); err != nil || out.Token == "" {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "STL не вернул токен"})
		return
	}
	if err := saveToken(out.Token); err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	l.token = out.Token
	_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

// handleOpen hands a link to the system browser. Restricted to the configured
// STL origin so this cannot become a local "launch anything" gadget.
func (l *link) handleOpen(w http.ResponseWriter, r *http.Request) {
	raw := r.URL.Query().Get("url")
	u, err := url.Parse(raw)
	base, berr := url.Parse(l.cfg.STLURL)
	if err != nil || berr != nil || u.Scheme != base.Scheme || u.Host != base.Host {
		http.Error(w, "ссылка не из STL", http.StatusForbidden)
		return
	}
	shellOpen(u.String())
	w.WriteHeader(http.StatusNoContent)
}

type httpError struct {
	code int
	body string
}

func (e httpError) Error() string { return fmt.Sprintf("STL ответил %d: %s", e.code, e.body) }

func (l *link) fetch(path, token string) ([]byte, error) {
	req, err := http.NewRequest(http.MethodGet, l.cfg.STLURL+path, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	return l.do(req)
}

func (l *link) post(path string, payload []byte) ([]byte, error) {
	req, err := http.NewRequest(http.MethodPost, l.cfg.STLURL+path, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	return l.do(req)
}

func (l *link) do(req *http.Request) ([]byte, error) {
	resp, err := l.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		return nil, httpError{code: resp.StatusCode, body: strings.TrimSpace(string(body))}
	}
	return body, nil
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return "?"
	}
	return h
}
