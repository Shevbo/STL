//go:build windows

package service

import (
	"fmt"
	"os"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/eventlog"
	"golang.org/x/sys/windows/svc/mgr"
)

// IsInteractive reports whether the process is running in an interactive session
// (console) rather than launched by the SCM as a service.
func IsInteractive() bool {
	isSvc, err := svc.IsWindowsService()
	if err != nil {
		// Conservatively treat an error as interactive so a console run still works.
		return true
	}
	return !isSvc
}

// agentService adapts the agent's RunFunc to the svc.Handler interface.
type agentService struct {
	run RunFunc
	el  *eventlog.Log
}

// Execute is the SCM entry point. It starts the agent worker, reports Running, and
// translates SCM Stop/Shutdown into a close of the worker's stop channel.
func (s *agentService) Execute(args []string, r <-chan svc.ChangeRequest, changes chan<- svc.Status) (ssec bool, errno uint32) {
	const accepted = svc.AcceptStop | svc.AcceptShutdown
	changes <- svc.Status{State: svc.StartPending}

	stopCh := make(chan struct{})
	workerDone := make(chan error, 1)
	go func() { workerDone <- s.run(stopCh) }()

	changes <- svc.Status{State: svc.Running, Accepts: accepted}

	for {
		select {
		case err := <-workerDone:
			// Worker exited on its own. Non-nil err -> non-zero exit so the SCM
			// recovery action (restart-on-failure) kicks in.
			if err != nil {
				if s.el != nil {
					_ = s.el.Error(1, fmt.Sprintf("agent worker exited: %v", err))
				}
				changes <- svc.Status{State: svc.Stopped}
				return false, 1
			}
			changes <- svc.Status{State: svc.Stopped}
			return false, 0
		case c := <-r:
			switch c.Cmd {
			case svc.Interrogate:
				changes <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				changes <- svc.Status{State: svc.StopPending}
				close(stopCh)
				// Wait for the worker to drain, then report stopped.
				<-workerDone
				changes <- svc.Status{State: svc.Stopped}
				return false, 0
			default:
				if s.el != nil {
					_ = s.el.Warning(1, fmt.Sprintf("unexpected control request #%d", c.Cmd))
				}
			}
		}
	}
}

// RunService runs the agent under the SCM. Call this only when IsInteractive()
// is false. It blocks until the SCM stops the service.
func RunService(run RunFunc) error {
	var elog *eventlog.Log
	if l, err := eventlog.Open(Name); err == nil {
		elog = l
		defer elog.Close()
		_ = elog.Info(1, fmt.Sprintf("%s starting", Name))
	}
	err := svc.Run(Name, &agentService{run: run, el: elog})
	if elog != nil {
		if err != nil {
			_ = elog.Error(1, fmt.Sprintf("%s failed: %v", Name, err))
		} else {
			_ = elog.Info(1, fmt.Sprintf("%s stopped", Name))
		}
	}
	return err
}

// recoveryActions restarts the service on failure: 5s, 10s, then 30s, with the
// failure count reset after one day of healthy running. This is the API-side
// equivalent of `sc failure` (see README for the sc.exe form).
func recoveryActions() ([]mgr.RecoveryAction, uint32) {
	actions := []mgr.RecoveryAction{
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 10 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
	}
	resetPeriodSec := uint32((24 * time.Hour) / time.Second)
	return actions, resetPeriodSec
}

// Install registers the agent as an auto-start Windows service pointing at the
// current executable, with the given extra args (e.g. --config). It also installs
// an event-log source and configures restart-on-failure so the agent survives both
// crashes and OS reboots.
func Install(args ...string) error {
	exePath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("locate executable: %w", err)
	}

	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to SCM (run as Administrator): %w", err)
	}
	defer m.Disconnect()

	if existing, err := m.OpenService(Name); err == nil {
		existing.Close()
		return fmt.Errorf("service %s already installed", Name)
	}

	cfg := mgr.Config{
		DisplayName:  DisplayName,
		Description:  Description,
		StartType:    mgr.StartAutomatic, // auto-start: survives OS reboot
		ServiceType:  windows.SERVICE_WIN32_OWN_PROCESS,
		ErrorControl: mgr.ErrorNormal,
	}
	s, err := m.CreateService(Name, exePath, cfg, args...)
	if err != nil {
		return fmt.Errorf("create service: %w", err)
	}
	defer s.Close()

	// Restart-on-failure (immunity to crashes + reboot survival together).
	actions, reset := recoveryActions()
	if err := s.SetRecoveryActions(actions, reset); err != nil {
		// Non-fatal: the service is installed; recovery can be set via sc.exe.
		fmt.Printf("warning: could not set recovery actions via API (%v). Set them with:\n  sc failure %s reset= 86400 actions= restart/5000/restart/10000/restart/30000\n", err, Name)
	}

	if err := eventlog.InstallAsEventCreate(Name, eventlog.Error|eventlog.Warning|eventlog.Info); err != nil {
		// Event-log registration is best-effort; the service still runs.
		fmt.Printf("warning: could not register event-log source: %v\n", err)
	}

	fmt.Printf("service %q installed (auto-start, restart-on-failure)\n", Name)
	return nil
}

// Uninstall stops (if running) and removes the service and its event-log source.
func Uninstall() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to SCM (run as Administrator): %w", err)
	}
	defer m.Disconnect()

	s, err := m.OpenService(Name)
	if err != nil {
		return fmt.Errorf("service %s not installed: %w", Name, err)
	}
	defer s.Close()

	// Best-effort stop before delete.
	_, _ = s.Control(svc.Stop)

	if err := s.Delete(); err != nil {
		return fmt.Errorf("delete service: %w", err)
	}
	_ = eventlog.Remove(Name)
	fmt.Printf("service %q uninstalled\n", Name)
	return nil
}

// Start starts an installed service.
func Start() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to SCM (run as Administrator): %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return fmt.Errorf("service %s not installed: %w", Name, err)
	}
	defer s.Close()
	if err := s.Start(); err != nil {
		return fmt.Errorf("start service: %w", err)
	}
	fmt.Printf("service %q started\n", Name)
	return nil
}

// Stop stops a running service and waits briefly for it to reach Stopped.
func Stop() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to SCM (run as Administrator): %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return fmt.Errorf("service %s not installed: %w", Name, err)
	}
	defer s.Close()

	status, err := s.Control(svc.Stop)
	if err != nil {
		return fmt.Errorf("stop service: %w", err)
	}
	timeout := time.Now().Add(15 * time.Second)
	for status.State != svc.Stopped {
		if time.Now().After(timeout) {
			return fmt.Errorf("timed out waiting for service to stop")
		}
		time.Sleep(300 * time.Millisecond)
		status, err = s.Query()
		if err != nil {
			return fmt.Errorf("query service status: %w", err)
		}
	}
	fmt.Printf("service %q stopped\n", Name)
	return nil
}
