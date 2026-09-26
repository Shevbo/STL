//go:build !windows

package ops

import "errors"

// Заглушки для сборки и тестов на хостере: агент живёт только на Windows-VDS,
// но собирается и проверяется на Linux, и чистая логика (вердикт, белый список
// логов) обязана тестироваться там же, где идёт CI.
var errWindowsOnly = errors.New("операция доступна только на Windows-VDS")

func processReport() (string, error)   { return "", errWindowsOnly }
func systemReport(Env) (string, error) { return "", errWindowsOnly }
func windowsReport() (string, error)   { return "", errWindowsOnly }
func healthReport(Env) (string, error) { return "", errWindowsOnly }
