//go:build !windows

package vdsguard

import "errors"

// ReadMem is Windows-only; other platforms (hoster build/tests) report absent.
func ReadMem() (MemStatus, bool) { return MemStatus{}, false }

// RestartQuik is Windows-only.
func RestartQuik(string) error { return errors.New("quik restart: windows only") }
