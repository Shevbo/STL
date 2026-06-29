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
		fmt.Sprintf(`copy /y "%s" "%s"`, stageExe, destExe),
		"if errorlevel 1 goto :fail",
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
