package ops

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerdictNamesTheDecidingMeasurement(t *testing.T) {
	cases := []struct {
		name      string
		hung      bool
		respMs    int64
		cpu       float64
		pongAgeMs int64
		want      string
	}{
		{"здоров", false, 3, 4, 1200, "жив, отклик 3 мс"},
		{"терминал не запущен", false, respNoWindow, 0, -1, "НЕ ЗАПУЩЕН"},
		{"Windows считает окно зависшим", true, 2, 100, 1000, "ЗАВИС"},
		{"окно не ответило за таймаут", false, respTimeout, 100, 2000, "ЗАВИС"},
		// 25.09.2026: окно отвечало, данные не шли. Вердикт обязан назвать это
		// зависанием, иначе диагностика снова скажет «всё хорошо».
		{"окно живо, Lua молчит минуту", false, 4, 5, healthPongDeadMs, "ЗАВИС"},
		{"медленный отклик", false, healthSlowRespMs, 5, 0, "медленно"},
		{"понг отстаёт", false, 3, 5, healthPongSlowMs, "медленно"},
		{"ядро в потолке", false, 3, healthCPUBusyPct, 0, "медленно"},
	}
	for _, c := range cases {
		got := verdict(c.hung, c.respMs, c.cpu, c.pongAgeMs)
		if !strings.Contains(got, c.want) {
			t.Fatalf("%s: verdict = %q, ожидалось содержащее %q", c.name, got, c.want)
		}
	}
}

func TestVerdictHangBeatsEverythingElse(t *testing.T) {
	// Быстрый отклик и свежий понг не должны прикрывать зависшее окно: именно
	// «один зелёный признак важнее остальных» и стоило семи часов простоя.
	got := verdict(true, 1, 0, 0)
	if !strings.HasPrefix(got, "ЗАВИС") {
		t.Fatalf("verdict = %q, ожидался ЗАВИС", got)
	}
}

func TestVerdictHealthyLineCarriesTheResponseTime(t *testing.T) {
	got := verdict(false, 7, 1, 100)
	if !strings.Contains(got, "7 мс") {
		t.Fatalf("вердикт обязан называть измеренный отклик: %q", got)
	}
}

func TestLogTailWhitelist(t *testing.T) {
	env := Env{
		QuikFolder: func() string { return `C:\QuikFinam` },
		AgentLog:   `C:\agent\agent.log`,
		RunnerLog:  `C:\agent\runner.log`,
	}
	if p, err := resolveLog(env, "quik"); err != nil ||
		filepath.Base(p) != "info.log" {
		t.Fatalf("quik -> %q %v", p, err)
	}
	// Путь в аргументе это не имя из списка, и попасть по нему нельзя.
	for _, bad := range []string{`..\..\Windows\win.ini`, `C:\QuikFinam\info.log`,
		"quik.log", "", "QUIK"} {
		if _, err := resolveLog(env, bad); err == nil {
			t.Fatalf("%q не в белом списке, но принят", bad)
		}
	}
}

func TestLogTailUnknownQuikFolderIsRefusedNotGuessed(t *testing.T) {
	// Lua ни разу не отвечал: каталог терминала неизвестен, и подставлять
	// «обычный» путь нельзя — прочитаем чужой файл и поверим ему.
	_, err := resolveLog(Env{QuikFolder: func() string { return "" }}, "quik")
	if err == nil || !strings.Contains(err.Error(), "неизвестен") {
		t.Fatalf("ожидался отказ из-за неизвестного каталога QUIK: %v", err)
	}
}

func TestTailReturnsTheLastLines(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.log")
	var b strings.Builder
	for i := 1; i <= 120; i++ {
		fmt.Fprintf(&b, "строка %d\n", i)
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	env := Env{AgentLog: path}
	out, err := tailLog(env, "agent", "10")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "строка 120") || strings.Contains(out, "строка 110") {
		t.Fatalf("ожидались последние 10 строк, получено:\n%s", out)
	}
	// Запрошенное сверх предела урезается, а не отказывается: диагностика во
	// время инцидента не должна спотыкаться о цифру.
	out, err = tailLog(env, "agent", "100000")
	if err != nil || !strings.Contains(out, "строка 1\n") {
		t.Fatalf("предел строк: %v\n%s", err, out)
	}
	if _, err := tailLog(env, "agent", "-5"); err == nil {
		t.Fatal("отрицательное число строк обязано быть отклонено")
	}
}

func TestHealthIsCataloguedAsReadOnly(t *testing.T) {
	op, err := Lookup("quik.health")
	if err != nil {
		t.Fatalf("quik.health обязана быть в каталоге: %v", err)
	}
	if op.NeedsConfirm() || op.RequiresPausedRobots {
		t.Fatal("проверка живости ничего не меняет и не должна требовать подтверждения")
	}
	if len(op.Args) != 0 {
		t.Fatal("quik.health не принимает аргументов")
	}
}

func TestRegisterReadsWiresEveryReadingOperation(t *testing.T) {
	// Сторож против «объявили в каталоге, забыли реализовать»: именно так
	// status.* и log.tail прожили от проекта до 26.09 нереализованными.
	r, _ := testRunner(true, false, 1000)
	RegisterReads(r, Env{})
	for _, id := range []string{"status.processes", "status.system", "status.windows",
		"quik.health", "log.tail"} {
		if _, ok := r.handlers[id]; !ok {
			t.Fatalf("читающая операция %s не зарегистрирована", id)
		}
	}
}
