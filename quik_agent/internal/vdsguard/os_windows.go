//go:build windows

package vdsguard

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	kernel32           = windows.NewLazySystemDLL("kernel32.dll")
	globalMemoryStatus = kernel32.NewProc("GlobalMemoryStatusEx")
)

type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

// ReadMem reports physical + commit memory via GlobalMemoryStatusEx.
func ReadMem() (MemStatus, bool) {
	var m memoryStatusEx
	m.Length = uint32(unsafe.Sizeof(m))
	r, _, _ := globalMemoryStatus.Call(uintptr(unsafe.Pointer(&m)))
	if r == 0 {
		return MemStatus{}, false
	}
	const mb = 1024 * 1024
	commitPct := uint32(0)
	if m.TotalPageFile > 0 {
		commitPct = uint32((m.TotalPageFile - m.AvailPageFile) * 100 / m.TotalPageFile)
	}
	return MemStatus{
		LoadPct:   m.MemoryLoad,
		TotalMB:   m.TotalPhys / mb,
		AvailMB:   m.AvailPhys / mb,
		CommitPct: commitPct,
	}, true
}

// EnsureAutostart makes the VDS reboot-proof with ZERO operator action: writes
// <exeDir>\start_all.bat (see AutostartBat) and registers/refreshes a logon
// scheduled task pointing at it. Called by the agent itself once the QUIK
// folder is known (from the Lua pong), idempotent via schtasks /F. No
// /RL HIGHEST: neither QUIK nor the agent needs elevation, and a plain
// current-user logon task registers without admin rights.
func EnsureAutostart(exeDir, exeName, quikFolder string) error {
	bat := filepath.Join(exeDir, "start_all.bat")
	if err := os.WriteFile(bat, []byte(AutostartBat(quikFolder, exeDir, exeName)), 0o644); err != nil {
		return fmt.Errorf("write %s: %w", bat, err)
	}
	out, err := exec.Command("schtasks", "/Create", "/F",
		"/TN", "ShectoryTradeStack", "/SC", "ONLOGON", "/TR", `"`+bat+`"`).CombinedOutput()
	if err != nil {
		return fmt.Errorf("schtasks: %v (%s)", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// RestartQuik force-kills the QUIK terminal (info.exe) and relaunches it from
// folder (the QUIK working dir the Lua pong reported). Requires the operator to
// have configured QUIK auto-login + the Lua-script autostart entry, or the
// terminal comes back to a login prompt.
func RestartQuik(folder string) error {
	_ = exec.Command("taskkill", "/IM", "info.exe", "/F").Run() // may not be running
	time.Sleep(5 * time.Second)
	exe := filepath.Join(folder, "info.exe")
	// START syntax: first quoted arg is the window title (see selfupdate).
	cmd := exec.Command("cmd", "/C", "start", "QUIK", "/D", folder, exe)
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start %s: %w", exe, err)
	}
	_ = cmd.Process.Release()
	return nil
}
