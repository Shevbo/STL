//go:build !windows

package vdsguard

// Заглушка для сборки и тестов на Linux (агент собирается на хостере).
func ProcPrivateMB(pid int) (uint64, bool) { return 0, false }
