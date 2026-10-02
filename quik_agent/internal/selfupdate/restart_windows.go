//go:build windows

package selfupdate

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// spawnRestart writes a detached .bat that waits for this process to exit, copies
// the staged exe over the installed one, cleans the stage dir, and restarts the
// same exe name. Mirrors PiranhaAI's apply-.bat.
func spawnRestart(exeDir, restartName, stage, stageExe string) error {
	stageQ := filepath.Clean(stage)
	destExe := filepath.Join(exeDir, restartName)
	batPath := filepath.Join(os.TempDir(), fmt.Sprintf("quik-agent-apply-%d.bat", time.Now().UnixNano()))

	lines := []string{
		"@echo off",
		"setlocal",
		"rem Shectory QUIK agent self-update: wait for exit, copy, restart",
		"ping -n 5 127.0.0.1 >nul",
		// The agent exits via os.Exit (no ctx cancel), which ORPHANS the runner
		// child: it kept trading against a dead pipe until its first console
		// write killed it, and its open exe handle could fail the companion
		// copy below (old runner shipped as the "new" one). Kill it here so the
		// new agent starts a clean pair; the runner re-warms from
		// runner_state.json (book + bars persisted).
		"taskkill /IM robot-runner.exe /F >nul 2>&1",
		fmt.Sprintf(`copy /y "%s" "%s"`, stageExe, destExe),
		"if errorlevel 1 goto :fail",
		// Companion binaries shipped in the same release zip (zero-touch satellite:
		// the bundled robot-runner rides the agent's own update channel).
		fmt.Sprintf(`if exist "%s" copy /y "%s" "%s"`,
			filepath.Join(stageQ, "robot-runner.exe"),
			filepath.Join(stageQ, "robot-runner.exe"),
			filepath.Join(exeDir, "robot-runner.exe")),
		// QLua feed/trade script rides the same channel -> <exeDir>\lua\, under a
		// VERSION-STAMPED name (shectory_trade_v<ver>.lua) so the operator's QUIK
		// load dialog shows which build he picks (operator requirement); older
		// versioned copies are swept first (the v* mask never matches the sidecar
		// shectory_trade_config.lua — operator's ACCOUNT etc — nor the plain name).
		// A plain-named copy is kept too so a pre-existing QUIK autostart entry
		// pointing at it still loads the CURRENT build after a terminal restart.
		fmt.Sprintf(`if exist "%s" if not exist "%s" mkdir "%s"`,
			filepath.Join(stageQ, "shectory_trade.lua"),
			filepath.Join(exeDir, "lua"),
			filepath.Join(exeDir, "lua")),
		fmt.Sprintf(`if exist "%s" del /q "%s" 2>nul`,
			filepath.Join(stageQ, "shectory_trade.lua"),
			filepath.Join(exeDir, "lua", "shectory_trade_v*.lua")),
		fmt.Sprintf(`if exist "%s" copy /y "%s" "%s"`,
			filepath.Join(stageQ, "shectory_trade.lua"),
			filepath.Join(stageQ, "shectory_trade.lua"),
			filepath.Join(exeDir, "lua", luaInstallName(stageQ))),
		fmt.Sprintf(`if exist "%s" copy /y "%s" "%s"`,
			filepath.Join(stageQ, "shectory_trade.lua"),
			filepath.Join(stageQ, "shectory_trade.lua"),
			filepath.Join(exeDir, "lua", "shectory_trade.lua")),
		fmt.Sprintf(`if exist "%s" rd /s /q "%s"`, stageQ, stageQ),
		fmt.Sprintf(`start "" /D "%s" "%s"`, exeDir, destExe),
		`del "%~f0"`,
		"goto :eof",
		":fail",
		fmt.Sprintf(`if exist "%s" rd /s /q "%s"`, stageQ, stageQ),
		`del "%~f0" 2>nul`,
		"exit /b 1",
	}
	body := strings.Join(lines, "\r\n") + "\r\n"
	if err := os.WriteFile(batPath, []byte(body), 0o644); err != nil {
		return err
	}

	// START syntax: the first quoted arg is the window title; without it Windows
	// treats the next token as the program name.
	cmd := exec.Command("cmd", "/C", "start", "/MIN", "Shectory QUIK Agent", "cmd", "/C", batPath)
	if err := cmd.Start(); err != nil {
		_ = os.Remove(batPath)
		return err
	}
	_ = cmd.Process.Release()
	return nil
}

// SpawnRelaunch планирует ПОДЪЁМ того же самого exe после выхода процесса: пишет
// отдельный .bat, который ждёт, добивает осиротевшего раннера и запускает агента
// снова. Ничего не копирует — это не обновление, а перезапуск.
//
// ЗАЧЕМ. Команда RESTART раньше была просто os.Exit(0) «для service manager».
// Service manager на боевом VDS никто не ставил: 02.10.2026 агент вышел по этой
// команде и НЕ ВЕРНУЛСЯ, живая торговля осталась без моста, а поднять его мог
// только оператор с консоли. Выключатель, выданный за перезапуск.
//
// Ошибку возвращаем, а не глотаем: вызывающий обязан НЕ выходить, если подъём не
// запланирован. Лучше остаться на старой сборке, чем лечь насовсем.
func SpawnRelaunch(exeDir string) error {
	exePath, err := os.Executable()
	if err != nil {
		return err
	}
	destExe := exePath
	if exeDir != "" {
		destExe = filepath.Join(exeDir, filepath.Base(exePath))
	}
	batPath := filepath.Join(os.TempDir(), fmt.Sprintf("quik-agent-relaunch-%d.bat", time.Now().UnixNano()))
	lines := []string{
		"@echo off",
		"setlocal",
		"rem Shectory QUIK agent: перезапуск по команде RESTART",
		"ping -n 5 127.0.0.1 >nul",
		// Тот же сирота, что и при самообновлении: агент выходит через os.Exit,
		// раннер остаётся с мёртвой трубой. Новый агент поднимет его сам, бары и
		// книга переживают перезапуск (runner_state.json).
		"taskkill /IM robot-runner.exe /F >nul 2>&1",
		fmt.Sprintf(`start "" /D "%s" "%s"`, exeDir, destExe),
		`del "%~f0"`,
	}
	body := strings.Join(lines, "\r\n") + "\r\n"
	if err := os.WriteFile(batPath, []byte(body), 0o644); err != nil {
		return err
	}
	cmd := exec.Command("cmd", "/C", "start", "/MIN", "Shectory QUIK Agent", "cmd", "/C", batPath)
	if err := cmd.Start(); err != nil {
		_ = os.Remove(batPath)
		return err
	}
	_ = cmd.Process.Release()
	return nil
}
