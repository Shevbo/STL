// Package config loads (and on first run interactively creates) agent_config.json.
//
// The Bearer token is NEVER stored in the config file. The config only records the
// NAME of the environment variable that holds the token; the value is read from the
// process environment at link time (see internal/link). This keeps secrets out of
// the on-disk config and out of version control.
package config

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// DefaultTokenEnv is the env var name suggested by the wizard.
const DefaultTokenEnv = "STL_QUIK_AGENT_TOKEN"

// Config is the on-disk agent configuration. No secret values live here.
type Config struct {
	// STLGRPCURL is the host:port (or dns:///host:port) of the STL gRPC endpoint
	// the agent dials out to. Example: "stl.example.com:8443".
	STLGRPCURL string `json:"stl_grpc_url"`
	// STLInsecure disables TLS for the dial (dev/LAN only). Production uses TLS.
	STLInsecure bool `json:"stl_insecure"`
	// TokenEnv is the NAME of the env var holding the Bearer token. Never the value.
	TokenEnv string `json:"token_env"`
	// QuikDataRoot is where the agent reads QUIK DDE output / writes its queue.
	QuikDataRoot string `json:"quik_data_root"`
	// PollIntervalSec drives market-data flush / command poll cadence.
	PollIntervalSec int `json:"poll_interval_sec"`
	// HeartbeatIntervalSec drives the Heartbeat frame cadence on the link.
	HeartbeatIntervalSec int `json:"heartbeat_interval_sec"`

	// ---- resilience / diagnostics thresholds (sub-agent D, additive) ----
	// All optional; absent fields fall back to the defaults below so older
	// agent_config.json files keep loading unchanged.

	// StaleTickMs: DDE is considered DEGRADED once the freshest tick is older
	// than this (ms). Below dde_down_ms it is a warning, not an outage.
	StaleTickMs int `json:"stale_tick_ms"`
	// DDEDownMs: DDE is considered DOWN once the freshest tick is older than
	// this (ms) while the DDE server is up; also the watchdog restart trigger.
	DDEDownMs int `json:"dde_down_ms"`
	// HeartbeatSec mirrors HeartbeatIntervalSec for resilience tuning; if set it
	// wins. Kept distinct so the watchdog cadence can be tuned without touching
	// the existing heartbeat field.
	HeartbeatSec int `json:"heartbeat_sec"`
	// DiagIntervalSec: cadence at which Diagnostics frames are emitted. Defaults
	// to the heartbeat cadence when unset.
	DiagIntervalSec int `json:"diag_interval_sec"`

	// ---- Phase 2: order / execution (sub-agent A, additive) ----
	// All optional; absent fields fall back to the defaults below so older
	// agent_config.json files keep loading unchanged. The master flag
	// QuikTradingEnabled defaults to FALSE: with it off, every order command is
	// rejected by the agent (Guard 3, defense in depth on top of STL).

	// QuikTradingEnabled is the master order flag. false (default) => the agent
	// rejects ALL place/cancel/execution commands. Must be explicitly set true (and
	// orders are still human-initiated + per-order limit-checked).
	QuikTradingEnabled bool `json:"quik_trading_enabled"`
	// TradeBridgePort is the loopback TCP port the agent serves for the QUIK Lua
	// script (the Lua connects as client). Default 50063.
	TradeBridgePort int `json:"trade_bridge_port"`
	// TradeQueueDir, when set, switches the Lua bridge from TCP to a shared-directory
	// file queue (cmd.jsonl out, evt.jsonl in) — used when the QUIK terminal has no
	// LuaSocket. Must match the Lua script's CONFIG.QUEUE_DIR. Empty => TCP.
	TradeQueueDir string `json:"trade_queue_dir"`
	// TradeClassCode is the QUIK CLASSCODE for placements, e.g. SPBFUT.
	TradeClassCode string `json:"trade_class_code"`
	// TradeAccountEnv is the NAME of the env var holding the trade account code.
	// Like the token, the account VALUE is never stored in the config; only the env
	// var name is. Empty => no account is sent (Lua may fill it).
	TradeAccountEnv string `json:"trade_account_env"`

	// ---- hard limits (agent-enforced, second line on top of STL) ----
	// MaxContractsPerOrder caps a single placement's quantity. Default 2.
	MaxContractsPerOrder int64 `json:"max_contracts_per_order"`
	// MaxWorkingContracts caps total resting quantity across all open orders. Default 2.
	MaxWorkingContracts int64 `json:"max_working_contracts"`
	// PriceCollarFrac is the max adverse fractional deviation from the order/arrival
	// price. Default 0.002 (0.2%).
	PriceCollarFrac float64 `json:"price_collar_frac"`
	// InstrumentWhitelist lists the only codes the agent will trade. Default
	// ["RIU6"]. Anything else is rejected.
	InstrumentWhitelist []string `json:"instrument_whitelist"`
	// DailyOrderCap caps placements per calendar day. Default 50.
	DailyOrderCap int `json:"daily_order_cap"`

	// ---- Robot hosting (agent-side execution). All optional/additive. ----

	// RunnerExe is an explicit path to the bundled robot-runner exe. Empty =>
	// <exeDir>\robot-runner.exe when that file exists, else hosting is disabled.
	RunnerExe string `json:"runner_exe"`
	// RunnerBridgePort is the loopback gRPC port the agent serves for the runner.
	RunnerBridgePort int `json:"runner_bridge_port"`
	// RobotsDataSubdir is the subdirectory (under exeDir) holding robots.json +
	// runner_state.json. Default "robots".
	RobotsDataSubdir string `json:"robots_data_subdir"`

	// ---- Status / recon showcase (Task 9, additive) ----

	// StatusPort is the loopback HTTP port the local operator showcase (internal/
	// status) listens on. Default 8071. Unlike every other numeric field in this
	// struct, an explicit 0 is a deliberate, PERSISTENT "disabled" state (an
	// operator may not want the local status server running at all) — see
	// applyDefaults, which only fills in the 8071 default when the key is
	// genuinely ABSENT from the config file, never when it reads back as 0.
	StatusPort int `json:"status_port"`
	// ReconManualOffset is the operator-declared manual position offset per
	// symbol (recon.Inputs.ManualOffset), edited via POST /api/manual-offset and
	// persisted here so it survives a restart. Default {} (nil/absent -> {}).
	ReconManualOffset map[string]int64 `json:"recon_manual_offset"`
	// StatusSnapshotMinSec floors how often the link mirrors the local status
	// JSON to STL (AgentStatusSnapshot), even when it changes every tick.
	// Default 5.
	StatusSnapshotMinSec int `json:"status_snapshot_min_sec"`
}

// RunnerExePath resolves the runner exe: explicit config path wins; else the
// default name next to the agent exe; "" (hosting disabled) when neither exists.
func (c *Config) RunnerExePath(exeDir string) string {
	if c.RunnerExe != "" {
		return c.RunnerExe
	}
	p := filepath.Join(exeDir, "robot-runner.exe")
	if _, err := os.Stat(p); err == nil {
		return p
	}
	return ""
}

// RobotsDataDir returns (and creates) the robot-hosting data directory.
func (c *Config) RobotsDataDir(exeDir string) string {
	d := filepath.Join(exeDir, c.RobotsDataSubdir)
	_ = os.MkdirAll(d, 0o755)
	return d
}

// ConfigPath returns the default config path next to the executable.
func ConfigPath(exeDir string) string {
	return filepath.Join(exeDir, "agent_config.json")
}

// applyDefaults fills in every additive field's default. raw is the config
// file's top-level JSON object (nil when there is no file yet, e.g. the
// wizard's fresh Config{}): it is consulted ONLY for StatusPort, which needs
// to distinguish "key absent" (backfill the 8071 default) from "key present
// as 0" (an explicit, persistent disable — must not be re-defaulted on every
// subsequent load). Every other field below uses the existing zero-value
// convention (a <=0 int or a nil map/slice means "absent"), same as the rest
// of this function.
func (c *Config) applyDefaults(raw map[string]json.RawMessage) {
	if c.TokenEnv == "" {
		c.TokenEnv = DefaultTokenEnv
	}
	if c.PollIntervalSec <= 0 {
		c.PollIntervalSec = 5
	}
	if c.HeartbeatIntervalSec <= 0 {
		c.HeartbeatIntervalSec = 15
	}
	if c.StaleTickMs <= 0 {
		c.StaleTickMs = 30_000
	}
	if c.DDEDownMs <= 0 {
		c.DDEDownMs = 60_000
	}
	if c.HeartbeatSec <= 0 {
		c.HeartbeatSec = c.HeartbeatIntervalSec
	}
	if c.DiagIntervalSec <= 0 {
		c.DiagIntervalSec = c.HeartbeatSec
	}

	// ---- Phase 2 defaults (additive). QuikTradingEnabled intentionally has NO
	// default: its zero value (false) is the safe master-off state. ----
	if c.TradeBridgePort <= 0 {
		c.TradeBridgePort = 50063
	}
	if c.TradeClassCode == "" {
		c.TradeClassCode = "SPBFUT"
	}
	if c.MaxContractsPerOrder <= 0 {
		c.MaxContractsPerOrder = 2
	}
	if c.MaxWorkingContracts <= 0 {
		c.MaxWorkingContracts = 2
	}
	if c.PriceCollarFrac <= 0 {
		c.PriceCollarFrac = 0.002
	}
	// nil (field absent) => default whitelist; an explicit [] in JSON stays empty
	// (fail-closed: nothing tradable) so an operator can lock trading down.
	if c.InstrumentWhitelist == nil {
		c.InstrumentWhitelist = []string{"RIU6"}
	}
	if c.DailyOrderCap <= 0 {
		c.DailyOrderCap = 50
	}

	// ---- Robot hosting defaults ----
	if c.RunnerBridgePort <= 0 {
		c.RunnerBridgePort = 50071
	}
	if c.RobotsDataSubdir == "" {
		c.RobotsDataSubdir = "robots"
	}

	// ---- Status / recon showcase defaults (Task 9, additive) ----
	if _, present := raw["status_port"]; !present {
		c.StatusPort = 8071
	}
	// nil (field absent) => default {}; an explicit {} in JSON stays {} (both
	// round-trip identically here since a present-but-empty map and an absent
	// one both decode as needing this default — unlike StatusPort there is no
	// "explicit 0 must persist" requirement for this field).
	if c.ReconManualOffset == nil {
		c.ReconManualOffset = map[string]int64{}
	}
	if c.StatusSnapshotMinSec <= 0 {
		c.StatusSnapshotMinSec = 5
	}
}

// loadFile parses an EXISTING config file and applies defaults — the read half
// of LoadOrInit, shared with ManualOffsets.Set (which must re-load fresh from
// disk before persisting, and must never route through the wizard). Read
// errors are returned as-is (os.IsNotExist distinguishable); parse errors are
// wrapped.
func loadFile(path string) (Config, error) {
	var cfg Config
	b, err := os.ReadFile(path)
	if err != nil {
		return cfg, err
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		return cfg, fmt.Errorf("parse config %s: %w", path, err)
	}
	// Consulted only so applyDefaults can tell "status_port absent" (backfill
	// 8071) apart from "status_port present as 0" (explicit, persistent
	// disable). Best-effort: b already unmarshaled successfully above, so
	// this cannot fail in a way that matters; a nil raw just means every key
	// reads as absent, which is the safe (defaulting) direction.
	var raw map[string]json.RawMessage
	_ = json.Unmarshal(b, &raw)
	cfg.applyDefaults(raw)
	return cfg, nil
}

// LoadOrInit loads the config, or runs the first-run wizard if it does not
// exist. A file that EXISTS but cannot be read is an error, never a wizard
// run (the wizard would overwrite a config a human needs to fix instead).
func LoadOrInit(path string, defaultDataRoot string) (Config, error) {
	cfg, err := loadFile(path)
	switch {
	case err == nil:
		return cfg, nil
	case os.IsNotExist(err):
		return runWizard(path, defaultDataRoot)
	default:
		return cfg, err
	}
}

func runWizard(path, defaultDataRoot string) (Config, error) {
	var cfg Config
	reader := bufio.NewReader(os.Stdin)
	fmt.Println("=== Shectory QUIK agent first-run setup ===")
	fmt.Println("(the Bearer token is NOT stored here; you give the env var NAME only)")
	cfg.STLGRPCURL = ask(reader, "STL gRPC URL (host:port)", "127.0.0.1:8443")
	cfg.STLInsecure = askBool(reader, "Disable TLS (dev/LAN only)?", false)
	cfg.TokenEnv = ask(reader, "Token env var NAME", DefaultTokenEnv)
	cfg.QuikDataRoot = ask(reader, "QUIK data root", defaultDataRoot)
	cfg.PollIntervalSec = askInt(reader, "Market-data flush interval (sec)", 5)
	cfg.HeartbeatIntervalSec = askInt(reader, "Heartbeat interval (sec)", 15)
	cfg.applyDefaults(nil) // fresh config: every key is genuinely absent

	if err := Save(path, cfg); err != nil {
		return cfg, err
	}
	fmt.Printf("Config saved: %s\n", path)
	if os.Getenv(cfg.TokenEnv) == "" {
		fmt.Printf("WARNING: env var %s is not set. Set it before the agent can authenticate.\n", cfg.TokenEnv)
	}
	return cfg, nil
}

// Save writes the config as indented JSON, atomically: full write to a
// sibling .tmp file, then rename over the target (same pattern as the robots
// store's flushLocked). This file carries token_env / whitelist / master
// flag — a torn write here bricks agent startup, so a crash mid-Save must
// leave the previous config intact.
func Save(path string, cfg Config) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func ask(r *bufio.Reader, prompt, def string) string {
	fmt.Printf("%s [%s]: ", prompt, def)
	v, _ := r.ReadString('\n')
	v = strings.TrimSpace(v)
	if v == "" {
		return def
	}
	return v
}

func askBool(r *bufio.Reader, prompt string, def bool) bool {
	d := "n"
	if def {
		d = "y"
	}
	v := strings.ToLower(ask(r, prompt+" (y/n)", d))
	switch v {
	case "y", "yes", "1", "true":
		return true
	default:
		return false
	}
}

func askInt(r *bufio.Reader, prompt string, def int) int {
	v := ask(r, prompt, fmt.Sprintf("%d", def))
	var n int
	if _, err := fmt.Sscanf(v, "%d", &n); err != nil || n <= 0 {
		return def
	}
	return n
}
