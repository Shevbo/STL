// Command quik-agent is the single-binary local QUIK agent for Windows.
//
// It reads QUIK market data over DDE (read-only), dials OUT to STL, and runs one
// long-lived bidi gRPC stream. Phase 1 is READ-ONLY: it never places, moves, or
// cancels orders. First run writes agent_config.json via an interactive wizard.
//
// The Bearer token is NEVER stored in the config or in this binary; the config
// records the NAME of an env var, and the value is read from the environment here.
//
// Resilience (sub-agent D): the agent can install/run as a Windows service that
// auto-starts on boot and restarts on failure; a watchdog supervises DDE liveness
// and restarts a hung DDE channel (read-only recovery); a health state machine
// emits Diagnostics + Alert frames; and a local Telegram fallback signals a link
// outage when the gRPC stream itself is down.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
	"time"

	"shectory/quik_agent/internal/config"
	"shectory/quik_agent/internal/health"
	"shectory/quik_agent/internal/link"
	"shectory/quik_agent/internal/notify"
	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/selfupdate"
	"shectory/quik_agent/internal/service"
	"shectory/quik_agent/internal/trade"
	"shectory/quik_agent/internal/watchdog"
)

// agentBuildRev is the build revision, injected at build time via
// -ldflags "-X main.agentBuildRevStr=<n>". Compared against the release source for
// self-update. Defaults to 0 (dev).
var agentBuildRevStr = "0"

const agentVersion = "shectory-quik-agent"

func buildRev() uint32 {
	v, err := strconv.ParseUint(agentBuildRevStr, 10, 32)
	if err != nil {
		return 0
	}
	return uint32(v)
}

// agentOptions bundles the resolved startup parameters shared by interactive and
// service execution paths.
type agentOptions struct {
	cfg          config.Config
	cfgPath      string
	exeDir       string
	token        string
	noSelfUpdate bool
}

func main() {
	var (
		cfgPath       string
		noSelfUpdate  bool
		serviceAction string
	)
	flag.StringVar(&cfgPath, "config", "", "config path (optional)")
	flag.BoolVar(&noSelfUpdate, "no-self-update", false, "disable self-update at startup and daily 03:00")
	flag.StringVar(&serviceAction, "service", "", "Windows service control: install|uninstall|start|stop|run")
	flag.Parse()

	// Windows service control subcommands (no DDE/link work; just SCM management).
	switch serviceAction {
	case "install":
		var extra []string
		if cfgPath != "" {
			extra = append(extra, "--config", cfgPath)
		}
		if noSelfUpdate {
			extra = append(extra, "--no-self-update")
		}
		exitOnErr(service.Install(extra...))
		return
	case "uninstall":
		exitOnErr(service.Uninstall())
		return
	case "start":
		exitOnErr(service.Start())
		return
	case "stop":
		exitOnErr(service.Stop())
		return
	case "run", "":
		// fall through to normal startup (run = explicit foreground)
	default:
		fmt.Println("unknown --service action:", serviceAction)
		os.Exit(2)
	}

	// Under the SCM there is no console for the first-run wizard; require an
	// existing config so resolveOptions never blocks on stdin.
	underSCM := serviceAction == "" && !service.IsInteractive()
	if underSCM {
		resolvedCfg := cfgPath
		if resolvedCfg == "" {
			exe, _ := os.Executable()
			resolvedCfg = config.ConfigPath(filepath.Dir(exe))
		}
		if _, statErr := os.Stat(resolvedCfg); statErr != nil {
			fmt.Println("service: missing config (run the agent once interactively first):", resolvedCfg)
			os.Exit(2)
		}
	}

	opt, err := resolveOptions(cfgPath, noSelfUpdate)
	if err != nil {
		fmt.Println(err)
		os.Exit(2)
	}

	// If the SCM launched us (not interactive and no explicit "run"), run under svc.
	if underSCM {
		if err := service.RunService(func(stop <-chan struct{}) error {
			return runAgent(opt, stop)
		}); err != nil {
			fmt.Println("service: fatal:", err)
			os.Exit(1)
		}
		return
	}

	// Interactive / foreground: translate OS signals into the stop channel.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	stopCh := make(chan struct{})
	go func() {
		<-ctx.Done()
		close(stopCh)
	}()

	if err := runAgent(opt, stopCh); err != nil {
		fmt.Println("agent: fatal:", err)
		os.Exit(1)
	}
	fmt.Println("agent: shut down")
}

func exitOnErr(err error) {
	if err != nil {
		fmt.Println("service:", err)
		os.Exit(1)
	}
}

// resolveOptions loads config and the token. It does not touch DDE or the link.
func resolveOptions(cfgPath string, noSelfUpdate bool) (agentOptions, error) {
	exe, _ := os.Executable()
	exeDir := filepath.Dir(exe)
	if cfgPath == "" {
		cfgPath = config.ConfigPath(exeDir)
	}

	defaultDataRoot := exeDir
	if runtime.GOOS == "windows" {
		defaultDataRoot = `C:\SHECTORY`
	}

	cfg, err := config.LoadOrInit(cfgPath, defaultDataRoot)
	if err != nil {
		return agentOptions{}, fmt.Errorf("config error: %w", err)
	}

	token := os.Getenv(cfg.TokenEnv)
	if token == "" {
		return agentOptions{}, fmt.Errorf("FATAL: env var %s (token_env in config) is not set. Cannot authenticate", cfg.TokenEnv)
	}

	return agentOptions{
		cfg:          cfg,
		cfgPath:      cfgPath,
		exeDir:       exeDir,
		token:        token,
		noSelfUpdate: noSelfUpdate,
	}, nil
}

// runAgent is the worker shared by interactive and service execution. It brings up
// DDE (via a restartable supervisor), starts the watchdog, runs the link, and
// returns when stop is closed. READ-ONLY: no order code anywhere in this path.
func runAgent(opt agentOptions, stop <-chan struct{}) error {
	cfg := opt.cfg
	host, _ := os.Hostname()

	fmt.Println("Shectory QUIK agent starting...")
	fmt.Println("  stl:    ", cfg.STLGRPCURL, "(insecure:", cfg.STLInsecure, ")")
	fmt.Println("  data:   ", cfg.QuikDataRoot)
	fmt.Println("  agent:  ", agentVersion, "build", buildRev())
	fmt.Println("  token:  ", cfg.TokenEnv, "(value not shown)")

	// Self-update source: same STL host over HTTPS, authenticated with the token.
	selfUpdateBase := os.Getenv("SHECTORY_AGENT_RELEASE_URL")
	var updSrc selfupdate.Source
	if selfUpdateBase != "" {
		updSrc = selfupdate.NewHTTPSource(selfUpdateBase, opt.token)
	}

	// Self-update on start.
	if !opt.noSelfUpdate && selfupdate.Enabled() && !selfupdate.EnvDisables() && updSrc != nil {
		if staged, err := selfupdate.MaybeSelfUpdate(updSrc, opt.exeDir, buildRev(), false); err != nil {
			fmt.Println("agent: self-update check:", err)
		} else if staged {
			os.Exit(0)
		}
	}

	// Bring up DDE through a restartable supervisor (Windows real, stub elsewhere).
	ddeSup, err := quikdde.NewSupervisor(cfg.QuikDataRoot)
	if err != nil {
		fmt.Println("quik DDE:", err)
	} else {
		fmt.Println("quik DDE: read-only reader started (disable: SHECTORY_DISABLE_DDE=1)")
	}
	defer ddeSup.Stop()

	// Daily self-update at 03:00.
	if !opt.noSelfUpdate && selfupdate.Enabled() && !selfupdate.EnvDisables() && updSrc != nil {
		go selfupdate.RunDailyAt(updSrc, opt.exeDir, buildRev(), 3, 0)
	}

	// Local out-of-band alert fallback (no-op unless its env vars are set).
	notifier := notify.FromEnv()
	if notifier.Enabled() {
		fmt.Println("agent: local Telegram fallback configured (link-down alerts)")
	}

	// Watchdog: read-only DDE recovery on staleness / dead server thread.
	wd := watchdog.New(watchdog.Config{
		CheckInterval: time.Duration(cfg.HeartbeatSec) * time.Second,
		StaleAfter:    time.Duration(cfg.DDEDownMs) * time.Millisecond,
	}, watchdog.Deps{
		Alive:       quikdde.Alive,
		FreshnessMs: quikdde.Default.FreshnessMs,
		HaveTicked:  func() bool { return quikdde.Default.LastMutationMs() > 0 },
		RestartDDE:  ddeSup.Restart,
		Logf:        func(f string, a ...any) { fmt.Printf("watchdog: "+f+"\n", a...) },
	})

	// Root context cancelled when stop is closed.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		<-stop
		cancel()
	}()

	go wd.Run(ctx)

	lk := link.New(link.Options{
		Target:            cfg.STLGRPCURL,
		Insecure:          cfg.STLInsecure,
		Token:             opt.token,
		AgentVersion:      agentVersion,
		BuildRev:          buildRev(),
		HostName:          host,
		QuikDataRoot:      cfg.QuikDataRoot,
		HeartbeatInterval: time.Duration(cfg.HeartbeatSec) * time.Second,
		PollInterval:      time.Duration(cfg.PollIntervalSec) * time.Second,
		Provider:          quikdde.Default,
		QuikAlive:         func() bool { return quikdde.Alive() },
		Thresholds: health.Thresholds{
			StaleTickMs: int64(cfg.StaleTickMs),
			DDEDownMs:   int64(cfg.DDEDownMs),
		},
		Reconnects: wd.Reconnects, // DDE-restart count surfaces in Diagnostics
		HaveTicked: func() bool { return quikdde.Default.LastMutationMs() > 0 },
		Notifier:   notifier,
		OnSelfUpdate: func() (bool, error) {
			if updSrc == nil {
				return false, fmt.Errorf("no release source (SHECTORY_AGENT_RELEASE_URL unset)")
			}
			return selfupdate.MaybeSelfUpdate(updSrc, opt.exeDir, buildRev(), true)
		},
		OnRestart: func() {
			fmt.Println("agent: RESTART command received — exiting for the service manager to restart")
			os.Exit(0)
		},
	})

	// ---- Phase 2: order / execution layer (HUMAN-INITIATED, master flag default off).
	// The link is the trade.Emitter; the manager enforces the hard limits + master
	// flag BEFORE anything reaches the Lua bridge. The bridge serves a loopback TCP
	// port the QUIK Lua script connects to. With quik_trading_enabled=false (default)
	// every order command is rejected, so this is inert unless explicitly enabled.
	tradeAccount := ""
	if cfg.TradeAccountEnv != "" {
		tradeAccount = os.Getenv(cfg.TradeAccountEnv) // VALUE never stored in config
	}
	guard := trade.NewGuard(trade.Limits{
		TradingEnabled:       cfg.QuikTradingEnabled,
		MaxContractsPerOrder: cfg.MaxContractsPerOrder,
		MaxWorkingContracts:  cfg.MaxWorkingContracts,
		PriceCollarFrac:      cfg.PriceCollarFrac,
		InstrumentWhitelist:  cfg.InstrumentWhitelist,
		DailyOrderCap:        cfg.DailyOrderCap,
	})
	bridge := trade.NewBridge(cfg.TradeBridgePort, nil, func(f string, a ...any) {
		fmt.Printf("trade-bridge: "+f+"\n", a...)
	})
	if cfg.TradeQueueDir != "" {
		bridge.SetQueueDir(cfg.TradeQueueDir) // file-queue transport (no LuaSocket)
	}
	mgr := trade.NewManager(trade.ManagerConfig{
		ClassCode: cfg.TradeClassCode,
		Account:   tradeAccount,
	}, bridge, guard, lk, func(f string, a ...any) {
		fmt.Printf("trade: "+f+"\n", a...)
	})
	bridge.SetHandler(mgr)                       // Lua events -> manager
	mgr.SetBookSource(ctx, quikdde.Default)      // local book for the 1b maker loop
	lk.SetTrade(mgr)                             // STL Phase 2 commands -> manager
	go func() {
		if err := bridge.Run(ctx); err != nil && ctx.Err() == nil {
			fmt.Println("trade-bridge:", err)
		}
	}()
	fmt.Printf("  trade:   bridge :%d  enabled=%v  whitelist=%v  (human-initiated only)\n",
		cfg.TradeBridgePort, cfg.QuikTradingEnabled, cfg.InstrumentWhitelist)

	if err := lk.Run(ctx); err != nil && ctx.Err() == nil {
		return fmt.Errorf("link: %w", err)
	}
	return nil
}
