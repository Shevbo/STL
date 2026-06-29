//go:build windows

package quikdde

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/text/encoding/charmap"
)

// DDE service name registered by the agent. QUIK is configured to export tables to
// this service / topic "data".
const ddeServiceName = "SHECTORY_QUIK"

// DDEML (ddeml.h)
const (
	CP_WINUNICODE = 1200
	CP_WINANSI    = 1004

	XCLASS_BOOL         = 0x1000
	XCLASS_DATA         = 0x2000
	XCLASS_FLAGS        = 0x4000
	XCLASS_NOTIFICATION = 0x8000
	XTYPF_NOBLOCK       = 0x0002

	XTYP_ADVDATA     = 0x0010 | XCLASS_FLAGS
	XTYP_ADVSTART    = 0x0030 | XCLASS_BOOL
	XTYP_CONNECT     = 0x0060 | XCLASS_BOOL | XTYPF_NOBLOCK
	XTYP_WILDCONNECT = 0x00E0 | XCLASS_DATA | XTYPF_NOBLOCK
	XTYP_REQUEST     = 0x00B0 | XCLASS_DATA
	XTYP_EXECUTE     = 0x0050 | XCLASS_FLAGS
	XTYP_POKE        = 0x0090 | XCLASS_FLAGS
	XTYP_DISCONNECT      = 0x00C0 | XCLASS_NOTIFICATION | XTYPF_NOBLOCK
	XTYP_ADVSTOP         = 0x0040 | XCLASS_NOTIFICATION
	XTYP_CONNECT_CONFIRM = 0x0070 | XCLASS_NOTIFICATION | XTYPF_NOBLOCK

	DDE_FACK = 0x8000

	DNS_REGISTER  = 0x0001
	DNS_FILTEROFF = 0x0008

	APPCLASS_STANDARD  = 0x00000000
	APPCMD_FILTERINITS = 0x00000020

	DMLERR_NO_ERROR = 0

	CF_TEXT        = 1
	CF_UNICODETEXT = 13

	wmQuit = 0x0012
)

var (
	modUser32 *windows.LazyDLL

	procGetMessageW        *windows.LazyProc
	procTranslateMessage   *windows.LazyProc
	procDispatchMessageW   *windows.LazyProc
	procPostThreadMessageW *windows.LazyProc

	procDdeInitializeW         *windows.Proc
	procDdeUninitialize        *windows.Proc
	procDdeNameService         *windows.Proc
	procDdeCreateStringHandleW *windows.Proc
	procDdeFreeStringHandle    *windows.Proc
	procDdeQueryStringW        *windows.Proc
	procDdeQueryStringA        *windows.Proc
	procDdeAccessData          *windows.Proc
	procDdeUnaccessData        *windows.Proc
	procDdeCreateDataHandle    *windows.Proc
	procDdeFreeDataHandle      *windows.Proc
	procDdeGetLastError        *windows.Proc

	ddemlOnce    sync.Once
	ddemlInitErr error
)

func init() {
	dir, err := windows.GetSystemDirectory()
	if err != nil || dir == "" {
		modUser32 = windows.NewLazySystemDLL("user32.dll")
	} else {
		modUser32 = windows.NewLazyDLL(filepath.Join(dir, "user32.dll"))
	}
	procGetMessageW = modUser32.NewProc("GetMessageW")
	procTranslateMessage = modUser32.NewProc("TranslateMessage")
	procDispatchMessageW = modUser32.NewProc("DispatchMessageW")
	procPostThreadMessageW = modUser32.NewProc("PostThreadMessageW")
}

func system32Path() string {
	dir, err := windows.GetSystemDirectory()
	if err == nil && dir != "" {
		return dir
	}
	if w := os.Getenv("windir"); w != "" {
		return filepath.Join(w, "System32")
	}
	return `C:\Windows\System32`
}

func appendUniquePath(list []string, p string) []string {
	p = strings.TrimSpace(p)
	if p == "" {
		return list
	}
	p = filepath.Clean(p)
	for _, x := range list {
		if strings.EqualFold(x, p) {
			return list
		}
	}
	return append(list, p)
}

func isDefaultDdemlModulePath(abs string) bool {
	windir := os.Getenv("windir")
	if windir == "" {
		windir = `C:\Windows`
	}
	sysDir := filepath.Clean(system32Path())
	abs = filepath.Clean(abs)
	dir := filepath.Clean(filepath.Dir(abs))
	base := strings.ToLower(filepath.Base(abs))
	if base != "ddeml.dll" && base != "user32.dll" {
		return false
	}
	if strings.EqualFold(dir, sysDir) {
		return true
	}
	if runtime.GOARCH == "386" {
		wow := filepath.Clean(filepath.Join(windir, "SysWOW64"))
		return strings.EqualFold(dir, wow)
	}
	return false
}

// ddemlCandidateFiles lists modules that export DDEML (DdeInitializeW etc).
// Per Microsoft these live in User32.dll; a standalone ddeml.dll is absent on some
// SKUs. We try an explicit override and ddeml.dll first, then user32.dll.
func ddemlCandidateFiles() []string {
	exe, err := os.Executable()
	if err != nil {
		exe = ""
	}
	exeDir := filepath.Dir(exe)
	windir := os.Getenv("windir")
	if windir == "" {
		windir = `C:\Windows`
	}
	var out []string
	out = appendUniquePath(out, os.Getenv("SHECTORY_DDE_DLL"))
	out = appendUniquePath(out, filepath.Join(system32Path(), "ddeml.dll"))
	if runtime.GOARCH == "386" {
		out = appendUniquePath(out, filepath.Join(windir, "SysWOW64", "ddeml.dll"))
	}
	out = appendUniquePath(out, filepath.Join(exeDir, "dde", "ddeml.dll"))
	out = appendUniquePath(out, filepath.Join(exeDir, "ddeml.dll"))
	out = appendUniquePath(out, filepath.Join(system32Path(), "user32.dll"))
	if runtime.GOARCH == "386" {
		out = appendUniquePath(out, filepath.Join(windir, "SysWOW64", "user32.dll"))
	}
	return out
}

func tryLoadDdemlAt(abs string) (windows.Handle, error) {
	dir := filepath.Dir(abs)
	base := filepath.Base(abs)
	strategies := []func() (windows.Handle, error){
		func() (windows.Handle, error) { return windows.LoadLibraryEx(abs, 0, 0) },
		func() (windows.Handle, error) {
			return windows.LoadLibraryEx(abs, 0, windows.LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)
		},
		func() (windows.Handle, error) {
			_ = windows.SetDllDirectory(dir)
			defer func() { _ = windows.SetDllDirectory("") }()
			return windows.LoadLibrary(base)
		},
	}
	var last error
	for _, fn := range strategies {
		h, err := fn()
		if err == nil && h != 0 {
			return h, nil
		}
		last = err
	}
	if last == nil {
		last = fmt.Errorf("load failed")
	}
	return 0, last
}

func loadDdemlModule() (*windows.DLL, error) {
	candidates := ddemlCandidateFiles()
	var lastErr error
	var tried []string
	for _, abs := range candidates {
		st, err := os.Stat(abs)
		if err != nil || st.IsDir() {
			continue
		}
		tried = append(tried, abs)
		h, err := tryLoadDdemlAt(abs)
		if err == nil && h != 0 {
			if !isDefaultDdemlModulePath(abs) && ddeDebug() {
				fmt.Println("quik DDE: DDEML module loaded from", abs, "(override / side-by-side / SHECTORY_DDE_DLL)")
			}
			return &windows.DLL{Name: abs, Handle: h}, nil
		}
		lastErr = err
	}
	hint := " Usually system32\\user32.dll exports DDEML. If Dde* is not found, copy ddeml.dll of the matching bitness into dde\\ next to the exe, or set SHECTORY_DDE_DLL."
	if len(tried) == 0 {
		return nil, fmt.Errorf("DDEML module not found (ddeml.dll, user32.dll, dde\\, SHECTORY_DDE_DLL).%s", hint)
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("load failed")
	}
	return nil, fmt.Errorf("%w — tried: %v.%s", lastErr, tried, hint)
}

func bindDdemlProcs(d *windows.DLL) error {
	var err error
	if procDdeInitializeW, err = d.FindProc("DdeInitializeW"); err != nil {
		return err
	}
	if procDdeUninitialize, err = d.FindProc("DdeUninitialize"); err != nil {
		return err
	}
	if procDdeNameService, err = d.FindProc("DdeNameService"); err != nil {
		return err
	}
	if procDdeCreateStringHandleW, err = d.FindProc("DdeCreateStringHandleW"); err != nil {
		return err
	}
	if procDdeFreeStringHandle, err = d.FindProc("DdeFreeStringHandle"); err != nil {
		return err
	}
	if procDdeQueryStringW, err = d.FindProc("DdeQueryStringW"); err != nil {
		return err
	}
	if procDdeQueryStringA, err = d.FindProc("DdeQueryStringA"); err != nil {
		return err
	}
	if procDdeAccessData, err = d.FindProc("DdeAccessData"); err != nil {
		return err
	}
	if procDdeUnaccessData, err = d.FindProc("DdeUnaccessData"); err != nil {
		return err
	}
	if procDdeCreateDataHandle, err = d.FindProc("DdeCreateDataHandle"); err != nil {
		return err
	}
	if procDdeFreeDataHandle, err = d.FindProc("DdeFreeDataHandle"); err != nil {
		return err
	}
	if procDdeGetLastError, err = d.FindProc("DdeGetLastError"); err != nil {
		return err
	}
	return nil
}

func ensureDdemlProcs() error {
	ddemlOnce.Do(func() {
		dll, err := loadDdemlModule()
		if err != nil {
			ddemlInitErr = err
			return
		}
		if err := bindDdemlProcs(dll); err != nil {
			ddemlInitErr = err
		}
	})
	return ddemlInitErr
}

var (
	ddeMu   sync.Mutex
	ddeInst uint32
	// During procDdeInitializeW.Call DDEML may invoke the callback before ddeInst
	// is assigned — read *ddeInitInstPtr in that window.
	ddeInitInstPtr *uint32
	ddeReady       bool
	globHSvc       uintptr
	globHTopic     uintptr
	lastItemMu     sync.Mutex
	lastItemData   = map[string][]byte{}
	lastItemFormat = map[string]uint32{}
)

func ddeGetLastError(inst uint32) uint32 {
	r, _, _ := procDdeGetLastError.Call(uintptr(inst))
	return uint32(r)
}

func ddeCallbackInstID() uint32 {
	if p := ddeInitInstPtr; p != nil {
		return *p
	}
	ddeMu.Lock()
	v := ddeInst
	ddeMu.Unlock()
	return v
}

func queryHSZ(inst uint32, hsz uintptr) string {
	if hsz == 0 {
		return ""
	}
	n, _, _ := procDdeQueryStringW.Call(uintptr(inst), hsz, 0, 0, uintptr(CP_WINUNICODE))
	if n > 0 {
		buf := make([]uint16, int(n)+1)
		procDdeQueryStringW.Call(uintptr(inst), hsz, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), uintptr(CP_WINUNICODE))
		s := windows.UTF16ToString(buf)
		if strings.TrimSpace(s) != "" {
			return s
		}
	}
	na, _, _ := procDdeQueryStringA.Call(uintptr(inst), hsz, 0, 0, uintptr(CP_WINANSI))
	if na == 0 {
		return ""
	}
	abuf := make([]byte, int(na)+1)
	procDdeQueryStringA.Call(uintptr(inst), hsz, uintptr(unsafe.Pointer(&abuf[0])), uintptr(len(abuf)), uintptr(CP_WINANSI))
	if i := bytes.IndexByte(abuf, 0); i >= 0 {
		abuf = abuf[:i]
	}
	decoded, err := charmap.Windows1251.NewDecoder().Bytes(abuf)
	if err == nil && len(decoded) > 0 {
		return string(decoded)
	}
	return string(abuf)
}

func accessHData(hdata uintptr) []byte {
	if hdata == 0 {
		return nil
	}
	var cb uint32
	ptr, _, _ := procDdeAccessData.Call(hdata, uintptr(unsafe.Pointer(&cb)))
	if ptr == 0 || cb == 0 {
		return nil
	}
	sl := unsafe.Slice((*byte)(unsafe.Pointer(ptr)), int(cb))
	out := make([]byte, len(sl))
	copy(out, sl)
	procDdeUnaccessData.Call(hdata)
	return out
}

func decodeDDEBytes(wFmt uint32, raw []byte) string {
	if len(raw) == 0 {
		return ""
	}
	switch wFmt {
	case CF_UNICODETEXT:
		if len(raw) < 2 {
			return ""
		}
		n := len(raw) / 2
		u := make([]uint16, n)
		for i := 0; i < n; i++ {
			u[i] = binary.LittleEndian.Uint16(raw[i*2:])
		}
		for len(u) > 0 && u[len(u)-1] == 0 {
			u = u[:len(u)-1]
		}
		return string(utf16.Decode(u))
	default:
		for len(raw) > 0 && raw[len(raw)-1] == 0 {
			raw = raw[:len(raw)-1]
		}
		decoded, err := charmap.Windows1251.NewDecoder().Bytes(raw)
		if err == nil && len(decoded) > 0 {
			return string(decoded)
		}
		return string(raw)
	}
}

// ddeDebug — full DDE trace (SHECTORY_DDE_DEBUG=1).
func ddeDebug() bool {
	switch strings.TrimSpace(strings.ToLower(os.Getenv("SHECTORY_DDE_DEBUG"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

// ddeVerboseData — per-ADVDATA/POKE/REQUEST noise (SHECTORY_DDE_VERBOSE=1).
func ddeVerboseData() bool {
	switch strings.TrimSpace(strings.ToLower(os.Getenv("SHECTORY_DDE_VERBOSE"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func ddeFmtName(wf uint32) string {
	switch wf {
	case CF_TEXT:
		return "CF_TEXT"
	case CF_UNICODETEXT:
		return "CF_UNICODETEXT"
	default:
		return fmt.Sprintf("fmt_%d", wf)
	}
}

func normTopic(s string) string {
	return strings.TrimSpace(s)
}

// quikTopicAndItem resolves the book "data" and the final item name from QUIK's
// mixed HSZ encodings ("[data]params", "data [params]", "data[params]", or
// topic="data" with item separate).
func quikTopicAndItem(hszTopic, hszItem string) (topicOK bool, itemName string) {
	t := normTopic(hszTopic)
	it := strings.TrimSpace(hszItem)
	if t == "" {
		return false, ""
	}
	if strings.EqualFold(t, "data") {
		return true, it
	}
	const bookPrefix = "[data]"
	if len(t) >= len(bookPrefix) && strings.EqualFold(t[:len(bookPrefix)], bookPrefix) {
		embedded := strings.TrimSpace(t[len(bookPrefix):])
		if it == "" {
			it = embedded
		}
		return true, it
	}
	if i := strings.IndexByte(t, '['); i > 0 {
		book := strings.TrimSpace(t[:i])
		if strings.EqualFold(book, "data") && len(t) >= i+2 && t[len(t)-1] == ']' {
			inner := strings.TrimSpace(t[i+1 : len(t)-1])
			if inner != "" {
				if it == "" {
					it = inner
				}
				return true, it
			}
		}
	}
	return false, it
}

// handleTablePayload merges a received DDE table into the in-memory provider.
// Read-only: the agent only consumes data here; it never writes back to QUIK.
func handleTablePayload(topicStr, itemName string, wFmt uint32, raw []byte) {
	lastItemMu.Lock()
	lastItemData[itemName] = append([]byte(nil), raw...)
	lastItemFormat[itemName] = wFmt
	lastItemMu.Unlock()

	sheet := SheetNameFromTopic(topicStr)
	if Default.MergeXlTable(sheet, itemName, raw) {
		if ddeVerboseData() {
			fmt.Printf("quik DDE: sheet %q item %q -> merged xl_table %d bytes\n", sheet, itemName, len(raw))
		}
		return
	}
	if ddeVerboseData() {
		text := decodeDDEBytes(wFmt, raw)
		fmt.Printf("quik DDE: sheet %q item %q: non-xltable payload (%s), %d bytes, %d text chars\n",
			sheet, itemName, ddeFmtName(wFmt), len(raw), len(text))
	}
}

func ddeCallback(uType, uFmt, hconv, hsz1, hsz2, hdata, dw1, dw2 uintptr) uintptr {
	ut := uint32(uType)
	wf := uint32(uFmt)
	inst := ddeCallbackInstID()
	if inst == 0 {
		return 0
	}

	switch ut {
	case XTYP_CONNECT:
		a := normTopic(queryHSZ(inst, hsz1))
		b := normTopic(queryHSZ(inst, hsz2))
		connectOK := func(topicStr, svcStr string) bool {
			if !strings.EqualFold(svcStr, ddeServiceName) {
				return false
			}
			ok, _ := quikTopicAndItem(topicStr, "")
			return ok
		}
		if connectOK(a, b) {
			if ddeVerboseData() {
				fmt.Printf("quik DDE: QUIK connected (topic=%q service=%q)\n", a, b)
			}
			return 1
		}
		if connectOK(b, a) {
			if ddeVerboseData() {
				fmt.Printf("quik DDE: QUIK connected (topic=%q service=%q)\n", b, a)
			}
			return 1
		}
		if ddeDebug() {
			fmt.Printf("quik DDE: XTYP_CONNECT rejected: hsz1=%q hsz2=%q (service %s; book data or [data]…)\n", a, b, ddeServiceName)
		}
		return 0

	case XTYP_WILDCONNECT:
		if globHSvc == 0 || globHTopic == 0 {
			return 0
		}
		type hszpair struct {
			svc, topic uintptr
		}
		var pairs [2]hszpair
		pairs[0] = hszpair{svc: globHSvc, topic: globHTopic}
		pairs[1] = hszpair{}
		cb := uint32(unsafe.Sizeof(pairs[0]) * 2)
		hRet, _, _ := procDdeCreateDataHandle.Call(
			uintptr(inst),
			uintptr(unsafe.Pointer(&pairs[0])),
			uintptr(cb),
			0,
			0,
			0,
			0,
		)
		if ddeDebug() {
			s1, s2 := queryHSZ(inst, hsz1), queryHSZ(inst, hsz2)
			fmt.Printf("quik DDE: XTYP_WILDCONNECT hsz1=%q hsz2=%q hData=0x%x cb=%d\n", s1, s2, hRet, cb)
		}
		return hRet

	case XTYP_ADVSTART:
		tAdv := queryHSZ(inst, hsz1)
		iAdv := queryHSZ(inst, hsz2)
		ok, item := quikTopicAndItem(tAdv, iAdv)
		if !ok || item == "" {
			if ddeDebug() {
				fmt.Printf("quik DDE: ADVSTART rejected — topic=%q itemHSZ=%q\n", tAdv, iAdv)
			}
			return 0
		}
		if ddeVerboseData() {
			fmt.Printf("quik DDE: QUIK started advise, item=%q\n", item)
		}
		return 1

	case XTYP_ADVDATA:
		ok, item := quikTopicAndItem(queryHSZ(inst, hsz1), queryHSZ(inst, hsz2))
		if !ok || item == "" {
			if ddeDebug() {
				fmt.Printf("quik DDE: ADVDATA rejected topic=%q itemHSZ=%q\n", queryHSZ(inst, hsz1), queryHSZ(inst, hsz2))
			}
			return 0
		}
		raw := accessHData(hdata)
		if ddeVerboseData() {
			if len(raw) == 0 {
				fmt.Printf("quik DDE: item %q: empty DDE block (%s)\n", item, ddeFmtName(wf))
			} else {
				fmt.Printf("quik DDE: item %q: DDE data, %s, %d bytes\n", item, ddeFmtName(wf), len(raw))
			}
		}
		if len(raw) > 0 {
			go handleTablePayload(queryHSZ(inst, hsz1), item, wf, raw)
		}
		return uintptr(DDE_FACK)

	case XTYP_REQUEST:
		ok, item := quikTopicAndItem(queryHSZ(inst, hsz1), queryHSZ(inst, hsz2))
		if !ok || item == "" {
			return 0
		}
		lastItemMu.Lock()
		raw := append([]byte(nil), lastItemData[item]...)
		wfLast := lastItemFormat[item]
		lastItemMu.Unlock()
		if len(raw) == 0 {
			return 0
		}
		if wfLast == 0 {
			wfLast = wf
		}
		hRet, _, _ := procDdeCreateDataHandle.Call(
			uintptr(inst),
			uintptr(unsafe.Pointer(&raw[0])),
			uintptr(len(raw)),
			0,
			hsz2,
			uintptr(wfLast),
			0,
		)
		return hRet

	case XTYP_ADVSTOP, XTYP_DISCONNECT:
		if ddeDebug() {
			fmt.Printf("quik DDE: XTYP 0x%x (advstop/disconnect) hconv=%x\n", ut, hconv)
		}
		return 1
	case XTYP_EXECUTE:
		if ddeDebug() {
			fmt.Printf("quik DDE: XTYP_EXECUTE hsz1=%q hsz2=%q\n", queryHSZ(inst, hsz1), queryHSZ(inst, hsz2))
		}
		return uintptr(DDE_FACK)
	case XTYP_POKE:
		ok, item := quikTopicAndItem(queryHSZ(inst, hsz1), queryHSZ(inst, hsz2))
		if !ok || item == "" {
			if ddeDebug() {
				fmt.Printf("quik DDE: POKE rejected topic=%q itemHSZ=%q\n", queryHSZ(inst, hsz1), queryHSZ(inst, hsz2))
			}
			return 0
		}
		raw := accessHData(hdata)
		if ddeVerboseData() {
			if len(raw) == 0 {
				fmt.Printf("quik DDE: item %q: empty POKE (%s)\n", item, ddeFmtName(wf))
			} else {
				fmt.Printf("quik DDE: item %q: POKE, %s, %d bytes\n", item, ddeFmtName(wf), len(raw))
			}
		}
		if len(raw) > 0 {
			go handleTablePayload(queryHSZ(inst, hsz1), item, wf, raw)
		}
		return uintptr(DDE_FACK)
	case XTYP_CONNECT_CONFIRM:
		if ddeDebug() {
			fmt.Printf("quik DDE: XTYP_CONNECT_CONFIRM hconv=%x\n", hconv)
		}
		return 0
	default:
		if ddeDebug() {
			fmt.Printf("quik DDE: unhandled XTYP uType=0x%x uFmt=%d (%s) hconv=%x hsz1=%q hsz2=%q hdata=%x\n",
				ut, wf, ddeFmtName(wf), hconv, queryHSZ(inst, hsz1), queryHSZ(inst, hsz2), hdata)
		}
		return 0
	}
}

func createHSZ(inst uint32, s string) (uintptr, error) {
	p, err := windows.UTF16PtrFromString(s)
	if err != nil {
		return 0, err
	}
	h, _, _ := procDdeCreateStringHandleW.Call(uintptr(inst), uintptr(unsafe.Pointer(p)), uintptr(CP_WINUNICODE))
	if h == 0 {
		return 0, fmt.Errorf("DdeCreateStringHandleW failed")
	}
	return h, nil
}

func freeHSZ(inst uint32, h uintptr) {
	if h != 0 {
		procDdeFreeStringHandle.Call(uintptr(inst), h)
	}
}

// Alive reports whether the DDE server thread is up and registered.
func Alive() bool {
	ddeMu.Lock()
	defer ddeMu.Unlock()
	return ddeReady
}

// StartDDE brings up the DDEML server: service SHECTORY_QUIK, topic data. QUIK is
// configured to export tables there ([data]<list>). Disable: SHECTORY_DISABLE_DDE=1.
func StartDDE(dataRoot string) (stop func(), err error) {
	if strings.TrimSpace(os.Getenv("SHECTORY_DISABLE_DDE")) == "1" {
		return func() {}, nil
	}

	if err := ensureDdemlProcs(); err != nil {
		return nil, fmt.Errorf("DDEML: %w", err)
	}

	started := make(chan error, 1)
	tidCh := make(chan uint32, 1)
	done := make(chan struct{})

	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		defer close(done)

		tidCh <- windows.GetCurrentThreadId()

		cb := syscall.NewCallback(ddeCallback)
		var inst uint32
		ddeInitInstPtr = &inst
		r, _, _ := procDdeInitializeW.Call(
			uintptr(unsafe.Pointer(&inst)),
			cb,
			uintptr(APPCLASS_STANDARD|APPCMD_FILTERINITS),
			0,
		)
		ddeInitInstPtr = nil
		if uint32(r) != DMLERR_NO_ERROR {
			started <- fmt.Errorf("DdeInitializeW failed: code %d (lasterr %d)", uint32(r), ddeGetLastError(inst))
			return
		}

		ddeMu.Lock()
		ddeInst = inst
		ddeMu.Unlock()

		defer func() {
			ddeMu.Lock()
			ddeInst = 0
			ddeReady = false
			ddeMu.Unlock()
			procDdeUninitialize.Call(uintptr(inst))
		}()

		hSvc, err := createHSZ(inst, ddeServiceName)
		if err != nil {
			started <- err
			return
		}
		defer freeHSZ(inst, hSvc)

		hTopic, err := createHSZ(inst, "data")
		if err != nil {
			started <- err
			return
		}
		defer freeHSZ(inst, hTopic)

		globHSvc = hSvc
		globHTopic = hTopic
		defer func() {
			globHSvc = 0
			globHTopic = 0
		}()

		ret, _, _ := procDdeNameService.Call(uintptr(inst), hSvc, 0, uintptr(DNS_REGISTER))
		if ret == 0 {
			le := ddeGetLastError(inst)
			started <- fmt.Errorf("DdeNameService DNS_REGISTER failed, DdeGetLastError=%d", le)
			return
		}
		_, _, _ = procDdeNameService.Call(uintptr(inst), 0, 0, uintptr(DNS_FILTEROFF))

		ddeMu.Lock()
		ddeReady = true
		ddeMu.Unlock()

		started <- nil
		fmt.Printf("quik DDE: %s | data — ready (read-only)\n", ddeServiceName)

		var m winMSG
	msgLoop:
		for {
			r, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
			switch int32(r) {
			case 0, -1:
				break msgLoop
			default:
				_, _, _ = procTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
				_, _, _ = procDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
			}
		}
	}()

	tid := <-tidCh
	err = <-started
	if err != nil {
		_, _, _ = procPostThreadMessageW.Call(uintptr(tid), uintptr(wmQuit), 0, 0)
		<-done
		return nil, err
	}

	stop = func() {
		_, _, _ = procPostThreadMessageW.Call(uintptr(tid), uintptr(wmQuit), 0, 0)
		<-done
	}
	return stop, nil
}
