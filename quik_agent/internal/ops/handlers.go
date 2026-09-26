package ops

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/encoding/charmap"
)

// Env — факты о машине, которые операции получают ИЗВНЕ, а не добывают сами.
// Возраст понга и RTT живут в accounts, пути логов знает main: ops не тянет их
// напрямую, иначе диагностический пакет окажется связан со всем агентом и его
// станет нельзя ни собрать отдельно, ни протестировать на хостере.
type Env struct {
	// QuikFolder — рабочий каталог терминала, его сообщает сам Lua в понге.
	// Пустая строка = терминал ни разу не отвечал, путь к info.log неизвестен.
	QuikFolder func() string
	AgentLog   string
	RunnerLog  string
	// Pong — возраст последнего понга Lua и его round trip, миллисекунды.
	// Отрицательные значения = понга не было ни разу.
	Pong func() (ageMs int64, rttMs int64)
}

// RegisterReads wires every READING operation of the catalog. Reads go first and
// separately from the state-changing ones on purpose: they change nothing, they
// need no confirmation, and it was exactly their absence that cost an hour of
// blind downtime on 25.09.2026.
func RegisterReads(r *Runner, env Env) {
	r.Register("status.processes", func(context.Context, map[string]string) (string, error) {
		return processReport()
	})
	r.Register("status.system", func(context.Context, map[string]string) (string, error) {
		return systemReport(env)
	})
	r.Register("status.windows", func(context.Context, map[string]string) (string, error) {
		return windowsReport()
	})
	r.Register("quik.health", func(context.Context, map[string]string) (string, error) {
		return healthReport(env)
	})
	r.Register("log.tail", func(_ context.Context, args map[string]string) (string, error) {
		return tailLog(env, args["file"], args["lines"])
	})
}

// QuikExe, RunnerExe — процессы, о которых спрашивают все читающие операции.
const (
	QuikExe   = "info.exe"
	RunnerExe = "robot-runner.exe"
)

// Пороги вердикта. Названы, а не вписаны в условия: их будет править оператор по
// живым наблюдениям, и он должен видеть, что именно правит.
const (
	// healthProbeTimeoutMs — сколько ждём ответа окна на пустое сообщение.
	// Здоровое окно QUIK отвечает за единицы миллисекунд; три секунды это уже
	// не «подумал», а «не разбирает очередь».
	healthProbeTimeoutMs = 3000
	// healthSlowRespMs — отклик выше этого называем медленным. Секунда на пустое
	// сообщение означает, что поток сообщений занят чем-то длинным, и торговые
	// события в этой очереди стоят за ним.
	healthSlowRespMs = 1000
	// healthPongSlowMs — понг Lua идёт раз в 5 с; втрое пропущенный = терминал
	// уже не крутит свой главный цикл нормально. Совпадает со SlowMs вотчдога.
	healthPongSlowMs = 15_000
	// healthPongDeadMs — минута молчания Lua это ЗАВИС, даже если окно бодрое.
	// 25.09.2026 терминал перестал отвечать в 16:29: агент был жив, связь с STL
	// зелёная, а данные не шли. Именно этот случай ловит порог по понгу, а не по
	// окну. Вотчдог перезапускает терминал только на 300 с; вердикт обязан
	// назвать беду раньше, потому что его читает человек.
	healthPongDeadMs = 60_000
	// healthCPUBusyPct — процент ОДНОГО ядра. Зависший info.exe обычно крутит
	// ядро в потолок, и это единственный признак, который виден ещё до того, как
	// окно перестанет отвечать.
	healthCPUBusyPct = 90
)

// Сигнальные значения отклика: измерение в миллисекундах не может их принять.
const (
	respTimeout  = -1 // окно не ответило за healthProbeTimeoutMs
	respNoWindow = -2 // главного окна info.exe нет (терминал не запущен)
)

// verdict turns four independent measurements into one line for a human.
//
// ЗАЧЕМ ЧЕТЫРЕ. Ни один признак сам по себе не отвечает на вопрос «жив ли QUIK».
// IsHungAppWindow молчит, пока окно разбирает очередь, — а данные при этом могут
// стоять (25.09). Понг Lua молчит при перезапуске скрипта — а терминал жив.
// Загрузка ядра в потолок бывает и при честном пересчёте графиков. Врут они
// по-разному, поэтому вердикт складывается из всех, и текст НАЗЫВАЕТ то
// измерение, которое его решило: оператору нужно знать, что чинить.
func verdict(hung bool, responseMs int64, cpuPct float64, pongAgeMs int64) string {
	if responseMs == respNoWindow {
		return fmt.Sprintf("QUIK НЕ ЗАПУЩЕН: главного окна %s нет", QuikExe)
	}
	switch {
	case hung:
		return "ЗАВИС: Windows считает окно не отвечающим" + pongTail(pongAgeMs)
	case responseMs == respTimeout:
		return fmt.Sprintf("ЗАВИС: окно не отвечает %d с (таймаут пробы)",
			int64(healthProbeTimeoutMs)/1000) + pongTail(pongAgeMs)
	case pongAgeMs >= healthPongDeadMs:
		// Окно отвечает, а данных нет — ровно 25.09.2026.
		return fmt.Sprintf("ЗАВИС: Lua молчит %d с, хотя окно отвечает за %d мс",
			pongAgeMs/1000, responseMs)
	}
	var slow []string
	if responseMs >= healthSlowRespMs {
		slow = append(slow, fmt.Sprintf("отклик %d мс", responseMs))
	}
	if pongAgeMs >= healthPongSlowMs {
		slow = append(slow, fmt.Sprintf("понг Lua %d с назад", pongAgeMs/1000))
	}
	if cpuPct >= healthCPUBusyPct {
		slow = append(slow, fmt.Sprintf("CPU %.0f%% ядра", cpuPct))
	}
	if len(slow) > 0 {
		return "медленно: " + strings.Join(slow, ", ")
	}
	return fmt.Sprintf("жив, отклик %d мс", responseMs)
}

func pongTail(pongAgeMs int64) string {
	if pongAgeMs < 0 {
		return ", понга Lua не было ни разу"
	}
	return fmt.Sprintf(", понг Lua %d с назад", pongAgeMs/1000)
}

// logTail* ограничивают чтение: хвост лога это диагностика, а не выгрузка файла
// в gRPC-стрим, по которому в это же время идут сделки.
const (
	logTailMaxLines     = 500
	logTailDefaultLines = 50
	logTailMaxBytes     = 256 * 1024
)

// resolveLog переводит ИМЯ цели в путь. Аргумент `file` принимает имя из белого
// списка, а НЕ путь: пути в этом канале нет вообще, и поэтому из него нечем
// выйти за пределы списка. Проверять чужой путь на «..» и симлинки пришлось бы
// правильно с первого раза, а имя из трёх вариантов проверять не нужно.
func resolveLog(env Env, name string) (string, error) {
	switch name {
	case "quik":
		folder := ""
		if env.QuikFolder != nil {
			folder = env.QuikFolder()
		}
		if folder == "" {
			return "", fmt.Errorf("каталог QUIK неизвестен: Lua ни разу не отвечал")
		}
		return filepath.Join(folder, "info.log"), nil
	case "agent":
		if env.AgentLog == "" {
			return "", fmt.Errorf("путь лога агента не задан")
		}
		return env.AgentLog, nil
	case "runner":
		if env.RunnerLog == "" {
			return "", fmt.Errorf("путь лога раннера не задан")
		}
		return env.RunnerLog, nil
	}
	return "", fmt.Errorf("лог %q не в белом списке: quik, agent, runner", name)
}

func tailLog(env Env, name, lines string) (string, error) {
	if name == "" {
		name = "quik"
	}
	path, err := resolveLog(env, name)
	if err != nil {
		return "", err
	}
	n := logTailDefaultLines
	if lines != "" {
		v, err := strconv.Atoi(lines)
		if err != nil || v <= 0 {
			return "", fmt.Errorf("lines должно быть положительным числом, получено %q", lines)
		}
		n = v
	}
	if n > logTailMaxLines {
		n = logTailMaxLines
	}
	return tailFile(path, n)
}

func tailFile(path string, n int) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return "", err
	}
	size := st.Size()
	off := size - logTailMaxBytes
	if off < 0 {
		off = 0
	}
	buf := make([]byte, size-off)
	if _, err := f.ReadAt(buf, off); err != nil && err != io.EOF {
		return "", err
	}
	// info.log у QUIK в cp1251: отданный как есть, он приходит оператору кашей,
	// а читают его именно ради русских строк про DDE и соединение.
	text := string(buf)
	if !utf8.ValidString(text) {
		if dec, err := charmap.Windows1251.NewDecoder().Bytes(buf); err == nil {
			text = string(dec)
		}
	}
	rows := strings.Split(strings.ReplaceAll(strings.TrimRight(text, "\r\n"), "\r\n", "\n"), "\n")
	if off > 0 && len(rows) > 1 {
		rows = rows[1:] // первая строка обрезана посередине окном чтения
	}
	if len(rows) > n {
		rows = rows[len(rows)-n:]
	}
	return fmt.Sprintf("%s (последние %d строк)\n%s",
		path, len(rows), strings.Join(rows, "\n")), nil
}
