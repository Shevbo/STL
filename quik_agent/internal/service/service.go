// Package service runs the agent as a Windows service (survives OS reboot) with a
// console-mode fallback for interactive runs. The platform-specific implementation
// lives in service_windows.go; a stub (service_stub.go) keeps the package building
// on non-Windows so the rest of the agent stays cross-platform.
//
// READ-ONLY constraint (Guard 3): this package only manages the agent process
// lifecycle (install / uninstall / start / stop / run-as-service). It contains no
// order code and reads no market data.
package service

// Name is the Windows service name (used by sc.exe and the SCM).
const Name = "ShectoryQuikAgent"

// DisplayName is the human-readable service name shown in services.msc.
const DisplayName = "Shectory QUIK Agent"

// Description appears in the service properties.
const Description = "Reads QUIK market data over DDE (read-only) and streams it to STL over gRPC."

// RunFunc is the agent's main worker. It must block until ctx-equivalent shutdown
// is requested; service mode calls it on a goroutine and signals stop by invoking
// the returned shutdown function passed via Hooks.
type RunFunc func(stop <-chan struct{}) error

// IsInteractive reports whether the process is running interactively (console)
// rather than as a Windows service. On non-Windows it is always true.
// Implemented per-platform.
//
// (declared here for documentation; the concrete implementation is build-tagged)
