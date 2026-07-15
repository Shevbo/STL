// Package vdsguard watches the two failure modes the rest of the stack cannot
// heal from the inside: a HUNG QUIK terminal (the Lua pong stops answering —
// every feed/order path dies with it) and VDS memory exhaustion (allocation
// failures kill processes at random). Detection is pong-based, not market-based:
// the Lua main-loop ping runs every 5s regardless of trading activity, so a
// quiet market never looks like a hang.
//
// Escalation: SLOW (pong late or RTT above the operator's ~10s "QUIK думает"
// threshold) -> WARN alert; HUNG (pong silent past HungMs) -> CRITICAL alert +
// forced terminal restart (taskkill info.exe + relaunch from the QUIK folder the
// pong itself reported). Restarts are cooldown-limited; QUIK must have
// auto-login + the Lua autostart entry for the cycle to close (operator setup).
//
// The guard is config-gated (quik_guard_disabled) so the operator can service
// the Lua script by hand without the guard fighting him.
package vdsguard

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	quikv1 "shectory/quik_agent/internal/pb"
)

type Config struct {
	Disabled    bool  // no restarts, no alerts (operator servicing the terminal)
	SlowMs      int64 // pong/rtt soft alarm; default 15s (operator: ">10s = думает")
	HungMs      int64 // forced restart threshold; default 300s
	CooldownMs  int64 // min gap between forced restarts; default 900s
	LowAvailMB  uint64 // low-memory alert when available RAM below; default 400
	HighLoadPct uint32 // ...or memory load at/above; default 92
}

func (c *Config) defaults() {
	if c.SlowMs == 0 {
		c.SlowMs = 15_000
	}
	if c.HungMs == 0 {
		c.HungMs = 300_000
	}
	if c.CooldownMs == 0 {
		c.CooldownMs = 900_000
	}
	if c.LowAvailMB == 0 {
		c.LowAvailMB = 400
	}
	if c.HighLoadPct == 0 {
		c.HighLoadPct = 92
	}
}

type Deps struct {
	// Pong returns (pongAgeMs, rttMs, quikFolder). rtt/age -1 = no pong ever.
	Pong func() (int64, int64, string)
	// Alert forwards to STL/Telegram; nil-safe.
	Alert func(sev quikv1.AlertSeverity, code, msg string)
	// RestartQuik kills and relaunches the terminal (platform-specific).
	RestartQuik func(folder string) error
	// Mem returns current OS memory status; ok=false when unavailable.
	Mem  func() (MemStatus, bool)
	Logf func(string, ...any)
	Now  func() int64
}

type MemStatus struct {
	LoadPct    uint32 `json:"load_pct"`     // GlobalMemoryStatusEx dwMemoryLoad
	TotalMB    uint64 `json:"total_mb"`
	AvailMB    uint64 `json:"avail_mb"`
	CommitPct  uint32 `json:"commit_pct"`   // committed/limit — allocation-failure proxy
}

// StatusView is what the local status page publishes (opaque-mirrored to STL).
type StatusView struct {
	QuikState     string     `json:"quik_state"` // OK | SLOW | HUNG | DISABLED
	PongAgeMs     int64      `json:"pong_age_ms"`
	RestartsToday int        `json:"restarts_today"`
	LastRestartMs int64      `json:"last_restart_ms,omitempty"`
	Mem           *MemStatus `json:"mem,omitempty"`
	LowMemory     bool       `json:"low_memory"`
}

type Guard struct {
	cfg  Config
	deps Deps

	mu            sync.Mutex
	lastRestartMs int64
	restartsToday int
	restartsDay   int64 // MSK day stamp the counter belongs to
	lastSlowAlert int64
	lastMemAlert  int64
	view          StatusView
}

func New(cfg Config, deps Deps) *Guard {
	cfg.defaults()
	if deps.Logf == nil {
		deps.Logf = func(string, ...any) {}
	}
	if deps.Now == nil {
		deps.Now = func() int64 { return time.Now().UnixMilli() }
	}
	return &Guard{cfg: cfg, deps: deps}
}

// Status returns the last computed view for the status page.
func (g *Guard) Status() StatusView {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.view
}

// Run ticks every 15s until ctx is done.
func (g *Guard) Run(ctx context.Context) {
	t := time.NewTicker(15 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			g.tick()
		}
	}
}

const (
	slowAlertEveryMs = 10 * 60 * 1000
	memAlertEveryMs  = 30 * 60 * 1000
)

func (g *Guard) tick() {
	now := g.deps.Now()
	pongAge, rtt, folder := g.deps.Pong()

	v := StatusView{QuikState: "OK", PongAgeMs: pongAge}

	// ---- OS memory ----
	if g.deps.Mem != nil {
		if m, ok := g.deps.Mem(); ok {
			mm := m
			v.Mem = &mm
			v.LowMemory = m.AvailMB < g.cfg.LowAvailMB || m.LoadPct >= g.cfg.HighLoadPct
			if v.LowMemory && g.throttle(&g.lastMemAlert, now, memAlertEveryMs) {
				g.alert(quikv1.AlertSeverity_ALERT_SEVERITY_WARN, "VDS_LOW_MEMORY",
					fmt.Sprintf("VDS память на пределе: свободно %d МБ из %d (load %d%%, commit %d%%) — риск отказов выделения",
						m.AvailMB, m.TotalMB, m.LoadPct, m.CommitPct))
			}
		}
	}

	// ---- QUIK responsiveness ----
	switch {
	case g.cfg.Disabled:
		v.QuikState = "DISABLED"
	case pongAge < 0:
		// never ponged since agent start: the agent may have just started while
		// QUIK/Lua boots — treat as SLOW (alerted, never force-restarted: there
		// is no evidence QUIK was ever alive this session to hang).
		v.QuikState = "SLOW"
		if g.throttle(&g.lastSlowAlert, now, slowAlertEveryMs) {
			g.alert(quikv1.AlertSeverity_ALERT_SEVERITY_WARN, "QUIK_NO_PONG",
				"нет ни одного pong от Lua с момента старта агента — проверь QUIK/скрипт")
		}
	case pongAge > g.cfg.HungMs:
		v.QuikState = "HUNG"
		g.maybeRestart(now, pongAge, folder)
	case pongAge > g.cfg.SlowMs || rtt > 10_000:
		v.QuikState = "SLOW"
		if g.throttle(&g.lastSlowAlert, now, slowAlertEveryMs) {
			g.alert(quikv1.AlertSeverity_ALERT_SEVERITY_WARN, "QUIK_SLOW",
				fmt.Sprintf("QUIK отвечает медленно: pong %d с назад, RTT %d мс", pongAge/1000, rtt))
		}
	}

	g.mu.Lock()
	v.RestartsToday, v.LastRestartMs = g.restartsToday, g.lastRestartMs
	g.view = v
	g.mu.Unlock()
}

func (g *Guard) maybeRestart(now, pongAge int64, folder string) {
	g.mu.Lock()
	day := mskDay(now)
	if g.restartsDay != day {
		g.restartsDay, g.restartsToday = day, 0
	}
	tooSoon := g.lastRestartMs != 0 && now-g.lastRestartMs < g.cfg.CooldownMs
	g.mu.Unlock()
	if tooSoon {
		return
	}
	if folder == "" {
		// cannot relaunch what we cannot locate — alert loudly, never kill blind
		if g.throttle(&g.lastSlowAlert, now, slowAlertEveryMs) {
			g.alert(quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL, "QUIK_HUNG",
				fmt.Sprintf("QUIK завис (pong %d с), но путь терминала неизвестен (нет cc3+ pong) — перезапусти вручную", pongAge/1000))
		}
		return
	}
	g.alert(quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL, "QUIK_HUNG_RESTART",
		fmt.Sprintf("QUIK завис (pong %d с назад) — принудительный перезапуск терминала из %s", pongAge/1000, folder))
	g.deps.Logf("QUIK hung (pong %ds ago): forced terminal restart from %s", pongAge/1000, folder)
	err := g.deps.RestartQuik(folder)
	g.mu.Lock()
	g.lastRestartMs = now
	g.restartsToday++
	g.mu.Unlock()
	if err != nil {
		g.alert(quikv1.AlertSeverity_ALERT_SEVERITY_CRITICAL, "QUIK_RESTART_FAILED",
			"перезапуск QUIK не удался: "+err.Error())
		g.deps.Logf("QUIK restart failed: %v", err)
	}
}

func (g *Guard) alert(sev quikv1.AlertSeverity, code, msg string) {
	if g.deps.Alert != nil {
		g.deps.Alert(sev, code, msg)
	}
}

// throttle returns true (and stamps) when at least every ms passed since *last.
func (g *Guard) throttle(last *int64, now, every int64) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	if *last != 0 && now-*last < every {
		return false
	}
	*last = now
	return true
}

func mskDay(nowMs int64) int64 {
	const day = int64(24 * 60 * 60 * 1000)
	const msk = int64(3 * 60 * 60 * 1000)
	return (nowMs + msk) / day
}

// AutostartBat renders <exeDir>\start_all.bat: QUIK first (terminal + saved
// layout + Lua autoload), a settle pause, then the agent. Pure — the Windows
// side writes it to disk and registers the logon task (EnsureAutostart).
func AutostartBat(quikDir, exeDir, exeName string) string {
	lines := []string{"@echo off",
		"rem Shectory autostart (written by the agent itself on every start)"}
	if quikDir != "" {
		lines = append(lines,
			fmt.Sprintf(`start "" "%s\info.exe"`, quikDir),
			"timeout /t 25 /nobreak >nul")
	}
	lines = append(lines,
		fmt.Sprintf(`cd /d "%s"`, exeDir),
		fmt.Sprintf(`start "" "%s\%s"`, exeDir, exeName))
	return strings.Join(lines, "\r\n") + "\r\n"
}
