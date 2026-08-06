// Command quik-agent is the single-binary local QUIK agent for Windows.
//
// Market data comes from the QLua file-queue publisher (shectory_trade.lua:
// ticks/books/tape/params/accounts); orders go through the same Lua bridge.
// The agent dials OUT to STL over one long-lived bidi gRPC stream and HOSTS
// live robots via the bundled robot-runner (loopback bridge). DDE is RETIRED:
// its reader exists only as a dormant legacy fallback (SHECTORY_ENABLE_DDE=1).
// First run writes agent_config.json via an interactive wizard.
//
// The Bearer token is NEVER stored in the config or in this binary; the config
// records the NAME of an env var, and the value is read from the environment here.
//
// Resilience: the agent can install/run as a Windows service that auto-starts
// on boot and restarts on failure; a watchdog supervises FEED liveness; a
// health state machine emits Diagnostics + Alert frames; and a local Telegram
// fallback signals a link outage when the gRPC stream itself is down.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/config"
	"shectory/quik_agent/internal/health"
	"shectory/quik_agent/internal/link"
	"shectory/quik_agent/internal/notify"
	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/robots"
	"shectory/quik_agent/internal/runner"
	"shectory/quik_agent/internal/selfupdate"
	"shectory/quik_agent/internal/service"
	"shectory/quik_agent/internal/status"
	"shectory/quik_agent/internal/trade"
	"shectory/quik_agent/internal/vdsguard"
	"shectory/quik_agent/internal/watchdog"
)

// runnerSrvTape forwards a tape batch to the runner bridge once it exists (the
// MD sink is wired before the robot-hosting block; nil-safe indirection).
var runnerSrvTape = func(trades []*quikv1.TapeTrade, code string) {}

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
	startedAt := time.Now() // Deps.UptimeSec (Task 9's status showcase)

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

	// Market data: the QLua file-queue publisher is the PRIMARY (and default
	// only) source. The DDE supervisor stays as a dormant legacy fallback —
	// its StartDDE is a no-op unless SHECTORY_ENABLE_DDE=1 resurrects it.
	ddeSup, err := quikdde.NewSupervisor(cfg.QuikDataRoot)
	if err != nil {
		fmt.Println("quik DDE:", err)
	} else if os.Getenv("SHECTORY_ENABLE_DDE") == "1" {
		fmt.Println("quik DDE: LEGACY reader resurrected (SHECTORY_ENABLE_DDE=1)")
	} else {
		fmt.Println("md: QLua file-queue feed (DDE retired; legacy fallback: SHECTORY_ENABLE_DDE=1)")
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

	// DDE is RETIRED (the QLua file-queue is the feed). This watchdog only
	// supervises the legacy DDE reader, and its sole action (RestartDDE) is a
	// no-op unless SHECTORY_ENABLE_DDE=1. Running it with DDE off fired
	// "DDE hung (dde server not alive) — read-only restart attempt" every cycle
	// and inflated the reconnect counter — a zombie. Feed staleness is already
	// classified by the health package. Start it only when DDE is enabled.
	if quikdde.LegacyEnabled() {
		go wd.Run(ctx)
	}

	// Объявлен ДО link.Options: QuikAlive ниже читает его понг, а сам стор
	// создаётся позже (ему нужен уже собранный bridge). Замыкание берёт значение
	// в момент вызова — к первому heartbeat стор уже присвоен.
	var accStore *accounts.Store

	lk := link.New(link.Options{
		Target:               cfg.STLGRPCURL,
		Insecure:             cfg.STLInsecure,
		Token:                opt.token,
		AgentVersion:         agentVersion,
		BuildRev:             buildRev(),
		HostName:             host,
		QuikDataRoot:         cfg.QuikDataRoot,
		HeartbeatInterval:    time.Duration(cfg.HeartbeatSec) * time.Second,
		PollInterval:         time.Duration(cfg.PollIntervalSec) * time.Second,
		Provider:             quikdde.Default,
		StatusSnapshotMinSec: cfg.StatusSnapshotMinSec,
		// Живость ТЕРМИНАЛА = свежий Lua-понг, а НЕ quikdde.Alive(): DDE выведен из
		// эксплуатации и по умолчанию выключен, поэтому Alive() всегда false и канал
		// QUIK вечно горел DOWN — панель заявок писала «НЕ готов» при исправно
		// исполняющихся заявках (2026-07-23). Постоянно красный индикатор хуже, чем
		// его отсутствие: настоящую остановку терминала на нём уже не заметить.
		// Понг — тот же сигнал, по которому сторож судит о зависании QUIK.
		QuikAlive: func() bool {
			if accStore == nil {
				return false
			}
			age := accStore.Snapshot().PongAgeMs
			return age >= 0 && age < int64(cfg.QuikGuardHungSec)*1000
		},
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
	// accStore holds the QUIK account snapshot (positions/orders/trades) and
	// agent<->QUIK clock health (RTT/drift/tape lag), fed by the QLua acc_pos/
	// acc_ord/acc_trd/pong publisher via bridge.SetAccSink below. The local
	// status showcase (Deps.Accounts) reads it; recon compares it against the
	// robots' believed books.
	accStore = accounts.New(func() int64 { return time.Now().UnixMilli() })
	// pingSentMs records the agent-clock send time of the most recent ping so the
	// pong handler can compute RTT on the agent clock ALONE (never the Lua-echoed
	// t0, which does not survive QUIK's 32-bit Lua integer encoding). One ping is
	// outstanding at the 5s cadence vs a ~ms RTT, so last-send is unambiguous.
	var pingSentMs atomic.Int64
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
	bridge.SetHandler(mgr) // Lua events -> manager
	// acc_pos/acc_ord/acc_trd/pong -> accStore: the account-snapshot half of
	// the QLua publisher (separate from the md/book/tape/param feed above).
	// Converters are type-tolerant (accounts.*FromRow); a malformed row is
	// skipped without corrupting the rest of the batch.
	bridge.SetAccSink(func(ev trade.AccEvent) {
		switch ev.Kind {
		case "pos":
			rows := make([]accounts.Position, 0, len(ev.Rows))
			for _, r := range ev.Rows {
				if p, ok := accounts.PositionFromRow(r); ok {
					rows = append(rows, p)
				}
			}
			accStore.SetPositions(rows)
		case "ord":
			rows := make([]accounts.Order, 0, len(ev.Rows))
			for _, r := range ev.Rows {
				if o, ok := accounts.OrderFromRow(r); ok {
					rows = append(rows, o)
				}
			}
			accStore.SetOrders(rows)
		case "trd":
			rows := make([]accounts.Trade, 0, len(ev.Rows))
			for _, r := range ev.Rows {
				if tr, ok := accounts.TradeFromRow(r); ok {
					rows = append(rows, tr)
				}
			}
			accStore.AddTrades(rows)
		case "money":
			// One row expected (limit_type 0); take the first convertible one.
			for _, r := range ev.Rows {
				if m, ok := accounts.MoneyFromRow(r); ok {
					accStore.SetMoney(m)
					break
				}
			}
		case "pong":
			// RTT is measured on the agent clock alone: feed the recorded send
			// time (pingSentMs), NOT the Lua-echoed ev.T0 (see pingSentMs decl).
			accStore.SetPong(pingSentMs.Load(), ev.TS, ev.ServerTime, ev.LastTradeTsMs)
			accStore.SetQuikFolder(ev.WF) // "" (old Lua) is ignored
		case "trans":
			accStore.AddTransReply(ev.TransID, ev.ResultCode, ev.OrderNum, ev.Text)
		}
	})
	// QLua market-data feed -> provider overlay: the PRIMARY data path for the
	// trading stack (DDE stays as fallback/passthrough — it needs manual QUIK
	// clicks and has repeatedly died in production).
	bridge.SetMDSink(func(ev trade.MDEvent) {
		if ev.IsParam {
			quikdde.Default.SetLuaParam(ev.Code, ev.PriceStep, ev.StepCost, ev.Margin)
			return
		}
		if ev.IsTape {
			// Backstop replay gate (layer 2; layer 1 lives in shectory_trade.lua):
			// drop trades whose exchange time (row[4], when the Lua provides it) is
			// stale — a restarted QUIK replays the whole day and receipt stamps make
			// replayed rows look live (2026-07-21: bars poisoned, robots piled longs).
			ev.Trades = quikdde.FilterStaleTapeRows(
				ev.Trades, time.Now().UnixMilli(), quikdde.TapeGateMaxAgeMs)
			trades := make([]*quikv1.TapeTrade, 0, len(ev.Trades))
			var lastPx float64
			for _, r := range ev.Trades {
				if len(r) >= 4 && r[0] > 0 {
					trades = append(trades, &quikv1.TapeTrade{Price: r[0], Qty: int64(r[1]),
						Side: int32(r[2]), TsUnixMs: int64(r[3])})
					lastPx = r[0]
				}
			}
			if len(trades) > 0 {
				runnerSrvTape(trades, ev.Code)
				quikdde.Default.SetLuaLast(ev.Code, lastPx) // /tick + UI follow the tape
				// NOTE: exchange lag is NOT measured here. Tape rows carry the
				// agent's OWN receipt stamp (see shectory_trade.lua OnAllTrade),
				// not the exchange's trade time, so timing off them would only
				// measure file-queue latency. accStore.ExchangeLagMs is instead
				// fed from the pong handler's last_trade_ts_ms (below), which IS
				// the exchange's own trade datetime.
			}
			return
		}
		if ev.IsBook {
			toLvls := func(rows [][]float64) []quikdde.BookLevel {
				out := make([]quikdde.BookLevel, 0, len(rows))
				for _, r := range rows {
					if len(r) >= 2 && r[0] > 0 && r[1] > 0 {
						out = append(out, quikdde.BookLevel{Price: r[0], Quantity: int64(r[1])})
					}
				}
				return out
			}
			quikdde.Default.SetLuaBook(ev.Code, toLvls(ev.Bids), toLvls(ev.Asks))
			return
		}
		quikdde.Default.SetLuaTick(ev.Code, ev.Last, ev.Bid, ev.Ask)
	})
	mgr.SetBookSource(ctx, quikdde.Default) // local book for the 1b maker loop
	lk.SetTrade(mgr)                        // STL Phase 2 commands -> manager
	go func() {
		if err := bridge.Run(ctx); err != nil && ctx.Err() == nil {
			fmt.Println("trade-bridge:", err)
		}
	}()
	fmt.Printf("  trade:   bridge :%d  enabled=%v  whitelist=%v  (human-initiated only)\n",
		cfg.TradeBridgePort, cfg.QuikTradingEnabled, cfg.InstrumentWhitelist)

	// Clock-sync probe: every 5s, ping Lua so accStore can track RTT/drift
	// (accStore.SetPong, fed back through the acc sink above once Lua echoes
	// the pong). Best-effort: no Lua client attached is the common idle case
	// and not worth logging every tick.
	go func() {
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				now := time.Now().UnixMilli()
				pingSentMs.Store(now) // record on the agent clock for RTT (see decl)
				_ = bridge.SendPing(now)
			}
		}
	}()

	// Часы VDS: ежечасный w32tm /resync. Часы этой машины дрейфовали минутами
	// (мешает свежести данных и измерению лага), а SSH-доступа к VDS нет —
	// агент единственный, кто может чинить их сам. Best-effort: w32tm требует
	// прав службы времени; отказ логируем и пробуем через час. Первый прогон
	// сразу при старте — дрейф чаще всего замечают после перезагрузки.
	go func() {
		resync := func() {
			out, err := exec.CommandContext(ctx, "w32tm", "/resync").CombinedOutput()
			if err != nil {
				fmt.Println("clock resync failed:", err, strings.TrimSpace(string(out)))
				return
			}
			fmt.Println("clock resync ok")
		}
		resync()
		t := time.NewTicker(time.Hour)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				resync()
			}
		}
	}()

	// ---- Robot hosting: local store + runner bridge + supervised runner ----
	// Zero-touch: the agent is the SINGLE entrypoint. It persists deployed
	// RobotSpecs (auto-resume after reboot), serves the loopback bridge, and
	// supervises the bundled robot-runner exe. STL is optional at runtime.
	robotStore, rsErr := robots.NewStore(cfg.RobotsDataDir(opt.exeDir))
	if rsErr != nil {
		fmt.Println("robots: store error:", rsErr)
	}
	// Hoisted so the Task 9 status/recon block below (which needs Runner too)
	// can see it after this if-block closes; nil when robot hosting is off.
	var runnerSrv *runner.Server
	if robotStore != nil {
		runnerSrv = runner.NewServer(runner.ServerCfg{
			Store:  robotStore,
			Ticks:  runner.ProviderTicks{P: quikdde.Default},
			Orders: mgr, Status: lk,
			Logf: func(f string, a ...any) { fmt.Printf("runner-bridge: "+f+"\n", a...) },
			// Шаг цены из QLua-фида параметров: PlaceRunnerOrder снапит цену
			// заявки раннера на биржевую сетку (раннер шага не знает).
			Steps: func(code string) float64 {
				for _, pr := range quikdde.Default.Params() {
					if pr.Code == code && pr.PriceStep > 0 {
						return pr.PriceStep
					}
				}
				return 0
			},
		})
		mgr.SetRunnerFan(runnerSrv.FanOrderEvent)
		runnerSrvTape = func(trades []*quikv1.TapeTrade, code string) {
			runnerSrv.FanTape(&quikv1.TapeBatch{Code: code, Trades: trades,
				ReceivedAtUnixMs: time.Now().UnixMilli()})
		}
		lk.SetRobots(robotStore, runnerSrv)
		go func() {
			addr := fmt.Sprintf("127.0.0.1:%d", cfg.RunnerBridgePort)
			if err := runnerSrv.Serve(ctx, addr); err != nil && ctx.Err() == nil {
				fmt.Println("runner-bridge:", err)
			}
		}()
		if exe := cfg.RunnerExePath(opt.exeDir); exe != "" {
			// Tee the runner's stdout/stderr (+ supervisor lifecycle lines) to
			// <runnerDir>\runner.log so the local status page serves it at
			// /logs/runner (the runner.log link on the stand). Console unchanged.
			logRunner := runner.FileTee(filepath.Join(filepath.Dir(exe), "runner.log"),
				func(f string, a ...any) { fmt.Printf(f+"\n", a...) })
			sup := runner.NewSupervisor(runner.SupervisorCfg{
				Start: runner.ExecStart(exe, []string{
					"--bridge", fmt.Sprintf("127.0.0.1:%d", cfg.RunnerBridgePort),
					"--data", cfg.RobotsDataDir(opt.exeDir),
				}, logRunner),
				Logf: func(f string, a ...any) { logRunner("runner-sup: "+f, a...) },
			})
			go sup.Run(ctx)
			fmt.Printf("  robots:  runner=%s  bridge=127.0.0.1:%d  persisted=%d\n",
				exe, cfg.RunnerBridgePort, len(robotStore.All()))
		} else {
			fmt.Println("  robots:  robot-runner.exe not found — robot hosting disabled")
		}
		// Journal auto-heal: robot-tagged QUIK trades missing from the runner's
		// believed book (fill lost between QUIK and persist — 2026-07-13) are
		// synthesized back through the normal event path. QUIK fact > agent belief.
		go runner.JournalSyncLoop(ctx, runnerSrv, accStore,
			func(f string, a ...any) { fmt.Printf("journal-sync: "+f+"\n", a...) })
		// Startup self-check traffic light: one aggregate readiness line after the
		// stack settles, so an operator (or log reader) sees what's up in ONE place.
		go func() {
			select {
			case <-ctx.Done():
				return
			case <-time.After(15 * time.Second):
			}
			ok := func(b bool) string {
				if b {
					return "ok"
				}
				return "DOWN"
			}
			// feed = the QLua file-queue market data (DDE retired; its legacy
			// server state is not worth an operator-facing field anymore).
			fmt.Printf("ready: feed=%s runner=%s robots=%d trading_enabled=%v status=:%d\n",
				ok(quikdde.Default.LastMutationMs() > 0),
				ok(runnerSrv.RunnerHealthy()), len(robotStore.All()), cfg.QuikTradingEnabled, cfg.StatusPort)
		}()
	}

	// ---- Local operator showcase: status.Deps + HTTP server + recon alert loop.
	// Depends on Robots+Runner (recon has nothing to reconcile without a hosted
	// robot registry), so this whole block is skipped when robot hosting itself
	// is unavailable (robotStore/runnerSrv nil — the rsErr/store-error case
	// above already explained why on the console).
	if robotStore != nil && runnerSrv != nil {
		aligner := &status.Aligner{
			Manager:  mgr,
			Runner:   runnerSrv,
			Provider: quikdde.Default,
			NowMs:    func() int64 { return time.Now().UnixMilli() },
		}

		// strategies_doc.json lives next to wherever the runner exe actually is
		// (or would be); Task 10 populates its content.
		runnerDir := opt.exeDir
		if p := cfg.RunnerExePath(opt.exeDir); p != "" {
			runnerDir = filepath.Dir(p)
		}

		// VDS guard: QUIK-hang watchdog (pong-silence -> forced terminal restart)
		// + OS memory health. Pong age is epoch-sized until the FIRST pong
		// (pongAtMs 0), so map "never ponged" to -1 via the rtt sentinel — the
		// guard must never kill a QUIK it has no evidence was ever alive.
		guard := vdsguard.New(vdsguard.Config{
			Disabled:     cfg.QuikGuardDisabled,
			ForceRestart: cfg.QuikGuardForceRestart,
			HungMs:       int64(cfg.QuikGuardHungSec) * 1000,
			CooldownMs:   int64(cfg.QuikGuardCooldownSec) * 1000,
		}, vdsguard.Deps{
			Pong: func() (int64, int64, string) {
				s := accStore.Snapshot()
				if s.RTTMs < 0 {
					return -1, -1, s.QuikFolder
				}
				return s.PongAgeMs, s.RTTMs, s.QuikFolder
			},
			Alert: func(sev quikv1.AlertSeverity, code, msg string) {
				_ = lk.EmitAlert(sev, code, msg)
			},
			RestartQuik: vdsguard.RestartQuik,
			Mem:         vdsguard.ReadMem,
			Logf:        func(f string, a ...any) { fmt.Printf("vds-guard: "+f+"\n", a...) },
		})
		go guard.Run(ctx)

		// Reboot immunity, zero operator action: once the QUIK folder is known
		// (first Lua pong), the agent writes start_all.bat and registers the
		// ShectoryTradeStack logon task itself, refreshed on every start (/F).
		// Files reach the VDS through the agent — the operator never downloads.
		if !cfg.AutostartDisabled {
			go func() {
				t := time.NewTicker(30 * time.Second)
				defer t.Stop()
				for {
					select {
					case <-ctx.Done():
						return
					case <-t.C:
					}
					folder := accStore.Snapshot().QuikFolder
					if folder == "" {
						continue // pre-cc3 Lua or QUIK still booting: retry
					}
					exe, err := os.Executable()
					if err != nil {
						fmt.Println("autostart: cannot resolve own exe:", err)
						return
					}
					if err := vdsguard.EnsureAutostart(
						filepath.Dir(exe), filepath.Base(exe), folder); err != nil {
						fmt.Println("autostart:", err)
					} else {
						fmt.Printf("autostart: logon task registered (QUIK=%s, agent=%s)\n",
							folder, exe)
					}
					return // once per agent start is enough (idempotent /F)
				}
			}()
		}

		deps := status.Deps{
			Accounts: accStore,
			Robots:   robotStore,
			Runner:   runnerSrv,
			Manager:  mgr,
			Provider: quikdde.Default,

			LinkUp:     lk.IsUp,
			Reconnects: wd.Reconnects,
			UptimeSec:  func() int64 { return int64(time.Since(startedAt).Seconds()) },
			MasterFlag: cfg.QuikTradingEnabled,
			BuildRev:   buildRev(),
			Version:    agentVersion,

			AlignExec: aligner.Execute,

			// ParamsSet persists a partial spec edit then pushes it live to the
			// connected runner. robots.ErrNotFound maps to status.ErrUnknownRobot
			// so the HTTP handler 404s instead of leaking the internal sentinel.
			ParamsSet: func(id string, upd status.ParamsUpdate) error {
				spec, err := robotStore.UpdateParams(id, upd.ParamsJSON, upd.Schedule, upd.MaxPosition)
				if err != nil {
					if errors.Is(err, robots.ErrNotFound) {
						return status.ErrUnknownRobot
					}
					return err
				}
				return runnerSrv.SendSetParams(id, spec.GetParamsJson())
			},

			// ModeSet flips a robot between paper and real — the real-money
			// arming action. Gated FLAT in both directions before the flip is
			// ever persisted: zero position (per the runner's last reported
			// status) AND no working/in-flight robot order (SnapshotWorking
			// includes unacked, OrderNum=="" entries, so this single loop also
			// covers "nothing in flight" with no separate pending-trans check).
			// A robot with NO entry yet in LastStatuses (runner reconnected but
			// hasn't sent its first ReportStatus) REFUSES rather than skipping
			// the position check: an absent status is not evidence of a flat
			// book, and flipping mode in that window could orphan a real open
			// position the agent simply hasn't heard about yet.
			ModeSet: func(id string, paper bool, confirmID string, force bool) error {
				spec := robotStore.Get(id)
				if spec == nil {
					return status.ErrUnknownRobot
				}
				if confirmID != id {
					return fmt.Errorf("подтверждение не совпадает: введите точный ID робота")
				}
				// Arming guard: refuse a robot whose max_position exceeds the effective
				// per-order/working cap — it could open via averaging but never close
				// (the whole-book exit is one order that CheckPlace rejects). Left a real
				// robot stuck long +14 unable to exit for hours (2026-07-21). Only on
				// arming (paper=false); disarm/paper never needs it.
				if !paper {
					lim := mgr.EffectiveLimits()
					if err := trade.CheckArmable(spec.GetMaxPositionContracts(),
						lim.GetMaxContractsPerOrder(), lim.GetMaxWorkingContracts()); err != nil {
						return err
					}
				}
				st := runnerSrv.LastStatuses()[id] // nil-safe: proto getters tolerate nil
				// forceDisarm: real->paper with the flat gate deliberately bypassed by
				// the operator. NEVER honoured for arming (paper=false) — arming real
				// money always requires a flat book.
				forceDisarm := force && paper
				if !forceDisarm {
					if _, ok := runnerSrv.LastStatuses()[id]; !ok {
						return fmt.Errorf("статус робота ещё не получен от раннера — не могу подтвердить нулевую позицию, повтори позже")
					}
					if st.GetPosition() != 0 {
						return fmt.Errorf("робот не в нуле (позиция %d): закрой позицию перед сменой режима", st.GetPosition())
					}
					for _, ws := range mgr.SnapshotWorking() {
						if rid, ok := trade.RobotIDFromClientID(ws.ClientID); ok && rid == id {
							ref := ws.OrderNum
							if ref == "" {
								ref = "(в полёте)"
							}
							return fmt.Errorf("у робота есть активная заявка %s: сними её перед сменой режима", ref)
						}
					}
				}
				newSpec, err := robotStore.SetPaper(id, paper)
				if err != nil {
					return err
				}
				if forceDisarm && st.GetPosition() != 0 {
					fmt.Printf("MODE: forced real->paper for %s with open position %d — the REAL position stays on the exchange, operator must close it manually\n",
						id, st.GetPosition())
				}
				return runnerSrv.SendDeploy(newSpec) // re-deploy flips paper
			},

			// ResetPaper zeroes a PAPER robot's fictional position/avg + clears its
			// working belief so its flat gate opens for arming. Refuses a REAL robot
			// (a real position must never be zeroed by a button).
			ResetPaper: func(id string) error {
				spec := robotStore.Get(id)
				if spec == nil {
					return status.ErrUnknownRobot
				}
				if !spec.GetPaper() {
					return fmt.Errorf("робот не в paper: обнуление только для бумажной торговли")
				}
				return runnerSrv.SendFixState(&quikv1.FixRobotState{
					RobotId:      id,
					SetPosition:  0,
					SetAvgPrice:  0,
					ClearWorking: true,
					Note:         "обнуление бумажной позиции (оператор)",
				})
			},

			// SetPosition force-writes a robot's believed position/avg (operator
			// manual correction after a desync, e.g. a fill that landed while the
			// Lua bridge was dead). Rides the SAME fix_state control the recon
			// aligner uses; runner persists immediately. Gated: exact typed
			// confirm + robot must be PAUSED (never rewrite a trading book live).
			SetPosition: func(id string, pos int64, avg float64, confirmID string, pnl *status.PnlFix) error {
				spec := robotStore.Get(id)
				if spec == nil {
					return status.ErrUnknownRobot
				}
				if confirmID != id {
					return fmt.Errorf("подтверждение не совпадает: введите точный ID робота")
				}
				if !robotStore.Paused(id) {
					return fmt.Errorf("робот должен быть на ПАУЗЕ: останови его перед ручной установкой позиции")
				}
				fix := &quikv1.FixRobotState{
					RobotId:      id,
					SetPosition:  pos,
					SetAvgPrice:  avg,
					ClearWorking: true,
					Note:         fmt.Sprintf("оператор: ручная установка позиции=%d avg=%.2f", pos, avg),
				}
				if pnl != nil {
					fix.SetPnl = true
					fix.SetRealizedGrossPts = pnl.RealizedGrossPts
					fix.SetCommissionPts = pnl.CommissionPts
					fix.Note += fmt.Sprintf(" pnl: gross=%.1f п. комиссия=%.1f п.",
						pnl.RealizedGrossPts, pnl.CommissionPts)
				}
				return runnerSrv.SendFixState(fix)
			},

			// Pause/resume from the local page: persist the flag (so a reconnect
			// replays it) AND push a live control to the connected runner. Blocks/
			// allows new entries; the open position is untouched.
			Pause: func(id string, paused bool) error {
				if robotStore.Get(id) == nil {
					return status.ErrUnknownRobot
				}
				if err := robotStore.SetPaused(id, paused); err != nil {
					return err
				}
				if paused {
					return runnerSrv.SendPause(id)
				}
				return runnerSrv.SendStart(id)
			},

			// The runner's stdout/stderr (+ supervisor lifecycle) are teed to
			// <runnerDir>\runner.log by FileTee above; expose it at /logs/runner.
			// The agent's OWN console still isn't filed (interactive => console,
			// Windows service => Event Log), so only "runner" is wired here.
			LogPaths: map[string]string{"runner": filepath.Join(runnerDir, "runner.log")},
			DocsPath: filepath.Join(runnerDir, "strategies_doc.json"),
			// Per-robot detailed logs: the runner writes <data_dir>/logs/<id>.log
			// (AgentRuntime.event); the stand's «Детальный лог робота» tails them.
			RobotLogDir: filepath.Join(cfg.RobotsDataDir(opt.exeDir), "logs"),
			NowMs:       func() int64 { return time.Now().UnixMilli() },
			VDSGuard:    guard.Status,
		}
		lk.SetStatusDeps(deps)

		if cfg.StatusPort != 0 {
			srv := status.NewServer(deps)
			srv.Addr = fmt.Sprintf("127.0.0.1:%d", cfg.StatusPort)
			go func() {
				<-ctx.Done()
				shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_ = srv.Shutdown(shutdownCtx)
			}()
			go func() {
				fmt.Println("status: serving on", srv.Addr)
				if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
					fmt.Println("status:", err)
				}
			}()
		} else {
			fmt.Println("status: disabled (status_port=0)")
		}

		// Recon alert loop: every 5s, re-evaluate the same recon computation
		// BuildStatus uses and feed the debounced alerter; any resulting spec
		// goes out over the live link (STL surfaces it same as any other Alert).
		go func() {
			var alerter status.ReconAlerter
			t := time.NewTicker(5 * time.Second)
			defer t.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-t.C:
					state, realInvolved := status.EvaluateRecon(deps)
					for _, spec := range alerter.Step(state, realInvolved, time.Now().UnixMilli()) {
						_ = lk.EmitAlert(spec.Severity, spec.Code, spec.Message)
					}
				}
			}
		}()
	}

	if err := lk.Run(ctx); err != nil && ctx.Err() == nil {
		return fmt.Errorf("link: %w", err)
	}
	return nil
}
