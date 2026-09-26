//go:build windows

// Реализация читающих операций под Windows. Всё здесь отвечает на один вопрос
// оператора, заданный 26.09.2026: «найди методы и проверяй, что квик жив и быстро
// откликается». Методов НЕСКОЛЬКО и они независимы намеренно, потому что 25.09
// терминал перестал отвечать в 16:29, торговля встала на семь часов, а каждый
// отдельный признак живости в тот момент показывал «всё хорошо».

package ops

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	user32                       = syscall.NewLazyDLL("user32.dll")
	procEnumWindows              = user32.NewProc("EnumWindows")
	procGetWindowTextW           = user32.NewProc("GetWindowTextW")
	procGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	procIsWindowVisible          = user32.NewProc("IsWindowVisible")
	procGetWindow                = user32.NewProc("GetWindow")
	// IsHungAppWindow не документирован, но экспортируется user32 со времён
	// Windows 2000 и отвечает ровно на наш вопрос: разбирает ли приложение
	// очередь сообщений. Это САМЫЙ прямой признак зависания — тот же, по которому
	// проводник рисует «(Не отвечает)» в заголовке.
	procIsHungAppWindow     = user32.NewProc("IsHungAppWindow")
	procSendMessageTimeoutW = user32.NewProc("SendMessageTimeoutW")

	kernel32ops              = syscall.NewLazyDLL("kernel32.dll")
	procGetTickCount64       = kernel32ops.NewProc("GetTickCount64")
	procGlobalMemoryStatusEx = kernel32ops.NewProc("GlobalMemoryStatusEx")
	procGetSystemTimes       = kernel32ops.NewProc("GetSystemTimes")

	psapiOps                 = syscall.NewLazyDLL("psapi.dll")
	procGetProcessMemoryInfo = psapiOps.NewProc("GetProcessMemoryInfo")
)

const (
	gwOwner         = 4 // GW_OWNER
	wmNull          = 0x0000
	smtoAbortIfHung = 0x0002
	// cpuSampleMs — окно измерения загрузки. Короткое: читающая операция идёт по
	// тому же стриму, по которому летят сделки, и задерживать его на секунду ради
	// красивой цифры нельзя.
	cpuSampleMs = 300
)

// ---- окна ----

type winInfo struct {
	hwnd    uintptr
	pid     uint32
	title   string
	visible bool
	top     bool // нет владельца, то есть окно верхнего уровня, а не диалог
}

// Коллектор один на пакет: syscall.NewCallback нельзя вызывать в цикле, каждый
// вызов занимает слот из общего лимита процесса и не освобождается.
var (
	enumMu   sync.Mutex
	enumAcc  []winInfo
	enumCB   = syscall.NewCallback(enumProc)
	titleBuf [512]uint16
)

func enumProc(hwnd uintptr, _ uintptr) uintptr {
	var pid uint32
	procGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
	n, _, _ := procGetWindowTextW.Call(hwnd,
		uintptr(unsafe.Pointer(&titleBuf[0])), uintptr(len(titleBuf)))
	vis, _, _ := procIsWindowVisible.Call(hwnd)
	owner, _, _ := procGetWindow.Call(hwnd, gwOwner)
	enumAcc = append(enumAcc, winInfo{
		hwnd:    hwnd,
		pid:     pid,
		title:   windows.UTF16ToString(titleBuf[:n]),
		visible: vis != 0,
		top:     owner == 0,
	})
	return 1 // продолжать перебор
}

// windowsOf returns the windows belonging to pid.
func windowsOf(pid uint32) []winInfo {
	enumMu.Lock()
	defer enumMu.Unlock()
	enumAcc = enumAcc[:0]
	procEnumWindows.Call(enumCB, 0)
	out := make([]winInfo, 0, 8)
	for _, w := range enumAcc {
		if w.pid == pid {
			out = append(out, w)
		}
	}
	return out
}

// ---- процессы ----

type procMemCountersEx struct {
	CB                         uint32
	PageFaultCount             uint32
	PeakWorkingSetSize         uintptr
	WorkingSetSize             uintptr
	QuotaPeakPagedPoolUsage    uintptr
	QuotaPagedPoolUsage        uintptr
	QuotaPeakNonPagedPoolUsage uintptr
	QuotaNonPagedPoolUsage     uintptr
	PagefileUsage              uintptr
	PeakPagefileUsage          uintptr
	PrivateUsage               uintptr
}

type procFacts struct {
	pid     uint32
	rssMB   uint64
	started time.Time
	cpu     time.Duration // суммарное процессорное время процесса
}

// pidsByName finds the pids of the named executables in ONE snapshot pass:
// открывать по снимку на процесс значит получить разные моменты времени в одной
// строке отчёта.
func pidsByName(want map[string]bool) map[string][]uint32 {
	out := map[string][]uint32{}
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return out
	}
	defer windows.CloseHandle(snap)
	var e windows.ProcessEntry32
	e.Size = uint32(unsafe.Sizeof(e))
	for err = windows.Process32First(snap, &e); err == nil; err = windows.Process32Next(snap, &e) {
		name := strings.ToLower(windows.UTF16ToString(e.ExeFile[:]))
		if want[name] {
			out[name] = append(out[name], e.ProcessID)
		}
	}
	return out
}

func filetimeTo100ns(f windows.Filetime) int64 {
	return int64(f.HighDateTime)<<32 | int64(uint32(f.LowDateTime))
}

// readProc собирает то, что видно по pid: RSS, время старта, процессорное время.
// ok=false когда процесса уже нет или прав не хватило — читающая операция из-за
// этого не падает, она просто говорит «не знаю» об этой строке.
func readProc(pid uint32) (procFacts, bool) {
	if pid == 0 {
		return procFacts{}, false
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return procFacts{}, false
	}
	defer windows.CloseHandle(h)
	f := procFacts{pid: pid}
	var pmc procMemCountersEx
	pmc.CB = uint32(unsafe.Sizeof(pmc))
	if r, _, _ := procGetProcessMemoryInfo.Call(uintptr(h),
		uintptr(unsafe.Pointer(&pmc)), uintptr(pmc.CB)); r != 0 {
		f.rssMB = uint64(pmc.WorkingSetSize) / (1024 * 1024)
	}
	var created, exited, kernel, user windows.Filetime
	if err := windows.GetProcessTimes(h, &created, &exited, &kernel, &user); err == nil {
		f.started = time.Unix(0, created.Nanoseconds())
		f.cpu = time.Duration(filetimeTo100ns(kernel)+filetimeTo100ns(user)) * 100
	}
	return f, true
}

// procCPUPct measures the process CPU over a short window, as a percent of ONE
// core. Именно ядро, а не машина: зависший info.exe крутит свой единственный
// поток сообщений в потолок, и на восьмиядерной VDS это дало бы 12% от машины —
// цифру, по которой ничего не видно.
func procCPUPct(pid uint32) float64 {
	a, ok := readProc(pid)
	if !ok {
		return 0
	}
	time.Sleep(cpuSampleMs * time.Millisecond)
	b, ok := readProc(pid)
	if !ok {
		return 0
	}
	return float64(b.cpu-a.cpu) / float64(cpuSampleMs*time.Millisecond) * 100
}

func selfExeName() string {
	p, err := os.Executable()
	if err != nil {
		return "quik-agent.exe"
	}
	return filepath.Base(p)
}

func processReport() (string, error) {
	self := selfExeName()
	want := map[string]bool{
		strings.ToLower(QuikExe):   true,
		strings.ToLower(RunnerExe): true,
		strings.ToLower(self):      true,
	}
	found := pidsByName(want)
	names := []string{QuikExe, RunnerExe, self}
	var b strings.Builder
	for _, n := range names {
		pids := found[strings.ToLower(n)]
		if len(pids) == 0 {
			fmt.Fprintf(&b, "%s НЕ ЗАПУЩЕН\n", n)
			continue
		}
		// Несколько экземпляров сами по себе беда: второй robot-runner.exe
		// торгует теми же роботами и дублирует заявки.
		if len(pids) > 1 {
			fmt.Fprintf(&b, "%s: ЭКЗЕМПЛЯРОВ %d\n", n, len(pids))
		}
		for _, pid := range pids {
			f, ok := readProc(pid)
			if !ok {
				fmt.Fprintf(&b, "%s pid %d: нет доступа к процессу\n", n, pid)
				continue
			}
			fmt.Fprintf(&b, "%s pid %d RSS %d МБ старт %s (%s назад) CPU-время %s\n",
				n, pid, f.rssMB, f.started.Format("2006-01-02 15:04:05"),
				dur(time.Since(f.started)), dur(f.cpu))
		}
	}
	return strings.TrimRight(b.String(), "\n"), nil
}

func dur(d time.Duration) string {
	d = d.Round(time.Second)
	if d < time.Hour {
		return fmt.Sprintf("%dм %dс", int(d.Minutes()), int(d.Seconds())%60)
	}
	h := int(d.Hours())
	return fmt.Sprintf("%dд %dч %dм", h/24, h%24, int(d.Minutes())%60)
}

// ---- система ----

type memStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

// ponytail: повторяет vdsguard.ReadMem намеренно. Вотчдог по проекту становится
// ПОТРЕБИТЕЛЕМ ops, импорт назад в ops дал бы цикл; двадцать строк дешевле общего
// третьего пакета ради одной структуры.
func readMemEx() (memStatusEx, bool) {
	var m memStatusEx
	m.Length = uint32(unsafe.Sizeof(m))
	if r, _, _ := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&m))); r == 0 {
		return m, false
	}
	return m, true
}

func systemCPUPct() float64 {
	read := func() (idle, busy int64, ok bool) {
		var i, k, u windows.Filetime
		r, _, _ := procGetSystemTimes.Call(uintptr(unsafe.Pointer(&i)),
			uintptr(unsafe.Pointer(&k)), uintptr(unsafe.Pointer(&u)))
		if r == 0 {
			return 0, 0, false
		}
		// kernel ВКЛЮЧАЕТ idle — вычитаем, иначе машина всегда занята на 100%.
		return filetimeTo100ns(i), filetimeTo100ns(k) + filetimeTo100ns(u), true
	}
	i1, b1, ok := read()
	if !ok {
		return 0
	}
	time.Sleep(cpuSampleMs * time.Millisecond)
	i2, b2, ok := read()
	if !ok || b2 == b1 {
		return 0
	}
	return float64((b2-b1)-(i2-i1)) / float64(b2-b1) * 100
}

func uptime() time.Duration {
	r1, r2, _ := procGetTickCount64.Call()
	ms := uint64(r1)
	// На 386 (агент публикуется и в 32 битах) 64-битный результат приходит
	// двумя регистрами: младшее в r1, старшее в r2.
	if ^uintptr(0)>>32 == 0 {
		ms |= uint64(r2) << 32
	}
	return time.Duration(ms) * time.Millisecond
}

func diskFreeMB(path string) (free, total uint64, ok bool) {
	p, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, 0, false
	}
	var avail, tot, totFree uint64
	if err := windows.GetDiskFreeSpaceEx(p, &avail, &tot, &totFree); err != nil {
		return 0, 0, false
	}
	const mb = 1024 * 1024
	return avail / mb, tot / mb, true
}

func systemReport(env Env) (string, error) {
	var b strings.Builder
	if m, ok := readMemEx(); ok {
		const mb = 1024 * 1024
		commitPct := uint64(0)
		if m.TotalPageFile > 0 {
			commitPct = (m.TotalPageFile - m.AvailPageFile) * 100 / m.TotalPageFile
		}
		fmt.Fprintf(&b, "память: свободно %d из %d МБ (загрузка %d%%), фиксация %d из %d МБ (%d%%)\n",
			m.AvailPhys/mb, m.TotalPhys/mb, m.MemoryLoad,
			(m.TotalPageFile-m.AvailPageFile)/mb, m.TotalPageFile/mb, commitPct)
	} else {
		b.WriteString("память: GlobalMemoryStatusEx не ответил\n")
	}
	// Диски только те, на которых лежим мы и терминал: 25.09 кончившееся место
	// было одной из версий, а место на чужих томах ни о чём не говорит.
	roots := map[string]bool{}
	if exe, err := os.Executable(); err == nil {
		roots[filepath.VolumeName(exe)+`\`] = true
	}
	if env.QuikFolder != nil {
		if f := env.QuikFolder(); f != "" {
			roots[filepath.VolumeName(f)+`\`] = true
		}
	}
	keys := make([]string, 0, len(roots))
	for k := range roots {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, root := range keys {
		if free, total, ok := diskFreeMB(root); ok {
			fmt.Fprintf(&b, "диск %s свободно %d из %d МБ\n", root, free, total)
		}
	}
	fmt.Fprintf(&b, "CPU машины %.0f%%, uptime %s", systemCPUPct(), dur(uptime()))
	return b.String(), nil
}

// ---- окна QUIK ----

func quikPID() (uint32, bool) {
	pids := pidsByName(map[string]bool{strings.ToLower(QuikExe): true})[strings.ToLower(QuikExe)]
	if len(pids) == 0 {
		return 0, false
	}
	return pids[0], true
}

func windowsReport() (string, error) {
	pid, ok := quikPID()
	if !ok {
		return QuikExe + " НЕ ЗАПУЩЕН: окон нет", nil
	}
	wins := windowsOf(pid)
	var rows []string
	for _, w := range wins {
		if w.title == "" {
			continue // безымянные служебные окна ни о чём не говорят оператору
		}
		mark := ""
		if !w.visible {
			mark = " [скрыто]"
		}
		rows = append(rows, w.title+mark)
	}
	sort.Strings(rows)
	if len(rows) == 0 {
		return fmt.Sprintf("%s pid %d: окон с заголовками нет", QuikExe, pid), nil
	}
	return fmt.Sprintf("%s pid %d, окон с заголовками %d:\n%s",
		QuikExe, pid, len(rows), strings.Join(rows, "\n")), nil
}

// ---- quik.health ----

// mainWindow picks the terminal's main window: верхнего уровня, видимое и с
// заголовком. Пробовать зависание на диалоге бессмысленно: очередь сообщений у
// потока одна, а диалог может быть чужой и модальный.
func mainWindow(pid uint32) (winInfo, bool) {
	var best winInfo
	found := false
	for _, w := range windowsOf(pid) {
		if !w.top || !w.visible || w.title == "" {
			continue
		}
		if !found || strings.HasPrefix(w.title, "QUIK") {
			best, found = w, true
		}
		if strings.HasPrefix(w.title, "QUIK") {
			break
		}
	}
	return best, found
}

// probeWindow измеряет реальное время отклика окна пустым сообщением WM_NULL.
// Пустое намеренно: оно ничего не делает в терминале, но проходит ровно тот путь,
// по которому идут все остальные — через очередь сообщений потока. Если очередь
// стоит, стоит и торговля, даже когда процесс жив и связь зелёная.
func probeWindow(hwnd uintptr) int64 {
	t0 := time.Now()
	var res uintptr
	r, _, _ := procSendMessageTimeoutW.Call(hwnd, wmNull, 0, 0,
		smtoAbortIfHung, healthProbeTimeoutMs, uintptr(unsafe.Pointer(&res)))
	if r == 0 {
		return respTimeout
	}
	ms := time.Since(t0).Milliseconds()
	if ms == 0 {
		ms = 1 // «ноль миллисекунд» читается как «не измеряли»
	}
	return ms
}

func healthReport(env Env) (string, error) {
	pongAge, rtt := int64(-1), int64(-1)
	if env.Pong != nil {
		pongAge, rtt = env.Pong()
	}
	pid, running := quikPID()
	if !running {
		return verdict(false, respNoWindow, 0, pongAge), nil
	}
	w, haveWin := mainWindow(pid)
	if !haveWin {
		// Процесс есть, главного окна нет — терминал либо ещё поднимается, либо
		// уже не свой. Для оператора это тот же «не отвечает».
		return fmt.Sprintf("%s\n%s pid %d, главного окна не найдено",
			verdict(false, respNoWindow, procCPUPct(pid), pongAge), QuikExe, pid), nil
	}
	hungR, _, _ := procIsHungAppWindow.Call(w.hwnd)
	hung := hungR != 0
	respMs := probeWindow(w.hwnd)
	cpu := procCPUPct(pid)

	var b strings.Builder
	b.WriteString(verdict(hung, respMs, cpu, pongAge))
	fmt.Fprintf(&b, "\n%s pid %d, окно %q", QuikExe, pid, w.title)
	fmt.Fprintf(&b, "\nIsHungAppWindow: %v", hung)
	switch respMs {
	case respTimeout:
		fmt.Fprintf(&b, "\nотклик окна: нет ответа за %d мс", healthProbeTimeoutMs)
	default:
		fmt.Fprintf(&b, "\nотклик окна: %d мс", respMs)
	}
	fmt.Fprintf(&b, "\nCPU процесса: %.0f%% ядра", cpu)
	if pongAge < 0 {
		b.WriteString("\nпонг Lua: не было ни разу")
	} else {
		fmt.Fprintf(&b, "\nпонг Lua: %d мс назад, RTT %d мс", pongAge, rtt)
	}
	return b.String(), nil
}
