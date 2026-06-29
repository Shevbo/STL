//go:build !windows

package quikdde

// StartDDE is a no-op on non-Windows (no DDEML available).
func StartDDE(dataRoot string) (stop func(), err error) {
	return func() {}, nil
}

// Alive is always false off Windows (no DDE server runs).
func Alive() bool { return false }
