//go:build !windows

package selfupdate

import "errors"

// spawnRestart is unsupported off Windows; MaybeSelfUpdate is gated by Enabled()
// so this is only reachable if called directly.
func spawnRestart(exeDir, restartName, stage, stageExe string) error {
	return errors.New("selfupdate: restart helper is Windows only")
}
