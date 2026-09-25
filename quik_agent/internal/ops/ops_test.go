package ops

import (
	"context"
	"strings"
	"testing"
)

func testRunner(paused, kill bool, now int64) (*Runner, *Confirmations) {
	c := NewConfirmations()
	r := NewRunner(Deps{
		RealRobotsPaused: func() bool { return paused },
		KillSwitch:       func() bool { return kill },
		Now:              func() int64 { return now },
	}, c)
	return r, c
}

func TestUnknownOperationIsRefused(t *testing.T) {
	r, _ := testRunner(true, false, 1000)
	r.Register("status.system", func(context.Context, map[string]string) (string, error) {
		return "ok", nil
	})
	if _, err := r.Run(context.Background(), "shell.exec",
		map[string]string{"cmd": "format C:"}, ""); err == nil {
		t.Fatal("операция вне каталога обязана быть отклонена")
	}
}

func TestReadOnlyOperationNeedsNoConfirmation(t *testing.T) {
	r, _ := testRunner(false, false, 1000)
	r.Register("status.processes", func(context.Context, map[string]string) (string, error) {
		return "info.exe pid 1234", nil
	})
	out, err := r.Run(context.Background(), "status.processes", nil, "")
	if err != nil || !strings.Contains(out, "1234") {
		t.Fatalf("читающая операция должна идти без подтверждения: %v %q", err, out)
	}
}

func TestStateChangingOperationWithoutConfirmationIsRefused(t *testing.T) {
	r, _ := testRunner(true, false, 1000)
	r.Register("runner.restart", func(context.Context, map[string]string) (string, error) {
		t.Fatal("не должно выполниться без подтверждения")
		return "", nil
	})
	if _, err := r.Run(context.Background(), "runner.restart", nil, ""); err == nil {
		t.Fatal("изменяющая операция без подтверждения обязана быть отклонена")
	}
}

func TestConfirmationIsBoundToOperationAndArgs(t *testing.T) {
	r, c := testRunner(true, false, 1000)
	r.Register("quik.restart", func(context.Context, map[string]string) (string, error) {
		return "restarted", nil
	})
	r.Register("quik.open_table", func(context.Context, map[string]string) (string, error) {
		return "opened", nil
	})
	// Оператор подтвердил ОТКРЫТИЕ таблицы сделок.
	args := map[string]string{"table": "deals"}
	c.Grant(Approval{ConfirmID: "t1", Binding: Binding("quik.open_table", args), IssuedMs: 1000})

	// Тем же токеном нельзя перезапустить QUIK.
	if _, err := r.Run(context.Background(), "quik.restart", nil, "t1"); err == nil {
		t.Fatal("подтверждение одной операции не должно годиться для другой")
	}
	// И нельзя открыть ДРУГУЮ таблицу.
	if _, err := r.Run(context.Background(), "quik.open_table",
		map[string]string{"table": "orders"}, "t1"); err == nil {
		t.Fatal("подтверждение привязано к аргументам")
	}
	// А ровно ту, что подтверждали, — можно.
	if _, err := r.Run(context.Background(), "quik.open_table", args, "t1"); err != nil {
		t.Fatalf("подтверждённая операция должна выполниться: %v", err)
	}
}

func TestConfirmationIsSingleUse(t *testing.T) {
	r, c := testRunner(true, false, 1000)
	r.Register("runner.restart", func(context.Context, map[string]string) (string, error) {
		return "ok", nil
	})
	c.Grant(Approval{ConfirmID: "t1", Binding: Binding("runner.restart", nil), IssuedMs: 1000})
	if _, err := r.Run(context.Background(), "runner.restart", nil, "t1"); err != nil {
		t.Fatalf("первое применение: %v", err)
	}
	if _, err := r.Run(context.Background(), "runner.restart", nil, "t1"); err == nil {
		t.Fatal("повторное применение токена обязано быть отклонено")
	}
}

func TestConfirmationExpires(t *testing.T) {
	c := NewConfirmations()
	c.Grant(Approval{ConfirmID: "t1", Binding: Binding("runner.restart", nil), IssuedMs: 0})
	err := c.Use("t1", "runner.restart", nil, ConfirmTTLMs+1)
	if err == nil || !strings.Contains(err.Error(), "просрочено") {
		t.Fatalf("подтверждение обязано истекать: %v", err)
	}
}

func TestHighRiskOperationDemandsPausedRealRobots(t *testing.T) {
	r, c := testRunner(false, false, 1000) // роботы НЕ на паузе
	r.Register("quik.restart", func(context.Context, map[string]string) (string, error) {
		t.Fatal("нельзя перезапускать QUIK при работающих реальных роботах")
		return "", nil
	})
	c.Grant(Approval{ConfirmID: "t1", Binding: Binding("quik.restart", nil), IssuedMs: 1000})
	_, err := r.Run(context.Background(), "quik.restart", nil, "t1")
	if err == nil || !strings.Contains(err.Error(), "паузу") {
		t.Fatalf("ожидался отказ из-за работающих роботов, получено: %v", err)
	}
}

func TestKillSwitchBlocksChangesButNotReads(t *testing.T) {
	r, c := testRunner(true, true, 1000) // стоп взведён
	r.Register("status.system", func(context.Context, map[string]string) (string, error) {
		return "мало памяти", nil
	})
	r.Register("runner.restart", func(context.Context, map[string]string) (string, error) {
		t.Fatal("при взведённом стопе изменяющая операция не должна идти")
		return "", nil
	})
	c.Grant(Approval{ConfirmID: "t1", Binding: Binding("runner.restart", nil), IssuedMs: 1000})
	if _, err := r.Run(context.Background(), "runner.restart", nil, "t1"); err == nil {
		t.Fatal("стоп обязан блокировать изменяющие операции")
	}
	if out, err := r.Run(context.Background(), "status.system", nil, ""); err != nil || out == "" {
		t.Fatalf("диагностика обязана работать и при взведённом стопе: %v", err)
	}
}

func TestUndeclaredArgumentIsRefused(t *testing.T) {
	r, _ := testRunner(true, false, 1000)
	r.Register("log.tail", func(context.Context, map[string]string) (string, error) {
		return "строки", nil
	})
	_, err := r.Run(context.Background(), "log.tail",
		map[string]string{"file": "info.log", "cmd": "& del *.*"}, "")
	if err == nil || !strings.Contains(err.Error(), "не принимает аргументы") {
		t.Fatalf("необъявленный аргумент обязан быть отклонён: %v", err)
	}
}

func TestRegisteringAnOperationOutsideTheCatalogPanics(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("регистрация операции вне каталога обязана падать на старте")
		}
	}()
	r, _ := testRunner(true, false, 1000)
	r.Register("shell.exec", func(context.Context, map[string]string) (string, error) {
		return "", nil
	})
}

func TestCatalogHasNoArbitraryExecution(t *testing.T) {
	// Сторож против будущей правки: каталог не должен обзавестись операцией,
	// исполняющей что попало. Ищем по ФОРМЕ имени, а не по подстроке — иначе
	// сюда попадает честный runner.restart, в котором «run» это раннер.
	for _, o := range Catalog {
		for _, bad := range []string{"exec", "shell", "cmd", "powershell", "eval", "script.run"} {
			if strings.HasPrefix(o.ID, bad+".") || strings.HasSuffix(o.ID, "."+bad) ||
				o.ID == bad {
				t.Fatalf("в каталоге появилась операция произвольного выполнения: %s", o.ID)
			}
		}
		// И ни одна операция не принимает аргумент, похожий на команду.
		for _, a := range o.Args {
			if a == "cmd" || a == "command" || a == "script" || a == "path" {
				t.Fatalf("операция %s принимает аргумент %q — это путь к shell", o.ID, a)
			}
		}
	}
}

func TestOnlyWhitelistedQuikOptionsCanBeSet(t *testing.T) {
	if err := CheckOption("dde.server"); err != nil {
		t.Fatalf("известная настройка должна приниматься: %v", err)
	}
	err := CheckOption("ExternalProgram")
	if err == nil || !strings.Contains(err.Error(), "не входит в список") {
		t.Fatalf("настройка вне белого списка обязана быть отклонена: %v", err)
	}
}

func TestReinstallAndSettingsAreCatalogued(t *testing.T) {
	for _, id := range []string{"quik.reinstall", "quik.set_option", "quik.dde_setup",
		"quik.profile_backup", "quik.profile_restore", "quik.settings"} {
		if _, err := Lookup(id); err != nil {
			t.Fatalf("операция %s обязана быть в каталоге: %v", id, err)
		}
	}
	// Переустановка обязана требовать паузы роботов и подтверждения.
	op, _ := Lookup("quik.reinstall")
	if !op.NeedsConfirm() || !op.RequiresPausedRobots {
		t.Fatal("переустановка QUIK обязана требовать подтверждения и паузы роботов")
	}
}
