//go:build windows

package vdsguard

import (
	"syscall"
	"unsafe"
)

// Память ЛЮБОГО процесса по pid — та самая «выделенная память» из Диспетчера
// задач (PrivateUsage), а не рабочий набор.
//
// ЗАЧЕМ. 20.08 машина QUIK дважды исчерпала лимит фиксации и не смогла создать
// стек нового потока: агент не поднялся, роботы не торговали 7 часов и 1 час.
// Кто именно съел 19 гигабайт, выяснял ОПЕРАТОР руками через PowerShell, потому
// что снаружи VDS видно только общий процент. Теперь снимок называет крупнейших
// потребителей сам, и следующий такой отказ диагностируется без человека.
var (
	psapi                  = syscall.NewLazyDLL("psapi.dll")
	getProcessMemoryInfo   = psapi.NewProc("GetProcessMemoryInfo")
	kernel32Proc           = syscall.NewLazyDLL("kernel32.dll")
	openProcess            = kernel32Proc.NewProc("OpenProcess")
	closeHandle            = kernel32Proc.NewProc("CloseHandle")
)

const processQueryLimited = 0x1000

type processMemoryCountersEx struct {
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

// ProcPrivateMB — выделенная память процесса в мегабайтах; ok=false, если
// процесса нет или прав не хватило (сторож из-за этого не падает).
func ProcPrivateMB(pid int) (uint64, bool) {
	if pid <= 0 {
		return 0, false
	}
	h, _, _ := openProcess.Call(uintptr(processQueryLimited), 0, uintptr(pid))
	if h == 0 {
		return 0, false
	}
	defer closeHandle.Call(h)
	var pmc processMemoryCountersEx
	pmc.CB = uint32(unsafe.Sizeof(pmc))
	r, _, _ := getProcessMemoryInfo.Call(h, uintptr(unsafe.Pointer(&pmc)), uintptr(pmc.CB))
	if r == 0 {
		return 0, false
	}
	return uint64(pmc.PrivateUsage) / (1024 * 1024), true
}
