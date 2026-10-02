//go:build !windows

package selfupdate

import "errors"

// spawnRestart is unsupported off Windows; MaybeSelfUpdate is gated by Enabled()
// so this is only reachable if called directly.
func spawnRestart(exeDir, restartName, stage, stageExe string) error {
	return errors.New("selfupdate: restart helper is Windows only")
}

// SpawnRelaunch вне Windows не поддержан. Возвращаем ОШИБКУ, а не тихое «ок»:
// вызывающий по ней решает не выходить, иначе агент лёг бы навсегда (02.10.2026).
func SpawnRelaunch(exeDir string) error {
	return errors.New("selfupdate: перезапуск поддержан только на Windows")
}
