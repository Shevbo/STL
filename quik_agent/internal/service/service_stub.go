//go:build !windows

package service

import "fmt"

// errNotWindows is returned by every service control on non-Windows platforms.
var errNotWindows = fmt.Errorf("windows service control is only available on Windows")

// IsInteractive always reports true off Windows: there is no SCM, so the agent
// always runs in console/foreground mode.
func IsInteractive() bool { return true }

// RunService is unsupported off Windows; the caller should run the worker directly.
func RunService(run RunFunc) error { return errNotWindows }

// Install is a no-op stub off Windows.
func Install(args ...string) error { return errNotWindows }

// Uninstall is a no-op stub off Windows.
func Uninstall() error { return errNotWindows }

// Start is a no-op stub off Windows.
func Start() error { return errNotWindows }

// Stop is a no-op stub off Windows.
func Stop() error { return errNotWindows }
