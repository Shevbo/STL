package main

// Thin Win32 layer. Pure syscalls via golang.org/x/sys/windows — no cgo, so the
// exe cross-builds and needs no C toolchain anywhere.

import (
	"errors"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	user32   = windows.NewLazySystemDLL("user32.dll")
	gdi32    = windows.NewLazySystemDLL("gdi32.dll")
	shell32  = windows.NewLazySystemDLL("shell32.dll")
	kernel32 = windows.NewLazySystemDLL("kernel32.dll")
	crypt32  = windows.NewLazySystemDLL("crypt32.dll")

	pRegisterClassExW = user32.NewProc("RegisterClassExW")
	pCreateWindowExW  = user32.NewProc("CreateWindowExW")
	pDefWindowProcW   = user32.NewProc("DefWindowProcW")
	pShowWindow       = user32.NewProc("ShowWindow")
	pSetWindowPos     = user32.NewProc("SetWindowPos")
	pGetMessageW      = user32.NewProc("GetMessageW")
	pTranslateMessage = user32.NewProc("TranslateMessage")
	pDispatchMessageW = user32.NewProc("DispatchMessageW")
	pPostQuitMessage  = user32.NewProc("PostQuitMessage")
	pPostMessageW     = user32.NewProc("PostMessageW")
	pLoadCursorW      = user32.NewProc("LoadCursorW")
	pLoadIconW        = user32.NewProc("LoadIconW")
	pLoadImageW       = user32.NewProc("LoadImageW")
	pSysParamsInfoW   = user32.NewProc("SystemParametersInfoW")
	pSetWinCompAttr   = user32.NewProc("SetWindowCompositionAttribute")
	pIsWindowVisible  = user32.NewProc("IsWindowVisible")
	pSetProcessDPI    = user32.NewProc("SetProcessDPIAware")
	pGetWindowRect    = user32.NewProc("GetWindowRect")
	pSetForegroundWin = user32.NewProc("SetForegroundWindow")
	pCreatePopupMenu  = user32.NewProc("CreatePopupMenu")
	pAppendMenuW      = user32.NewProc("AppendMenuW")
	pTrackPopupMenu   = user32.NewProc("TrackPopupMenu")
	pDestroyMenu      = user32.NewProc("DestroyMenu")
	pGetCursorPos     = user32.NewProc("GetCursorPos")

	pShellNotifyIconW = shell32.NewProc("Shell_NotifyIconW")
	pShellExecuteW    = shell32.NewProc("ShellExecuteW")
	pGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")
	pCreateMutexW     = kernel32.NewProc("CreateMutexW")

	pCryptProtectData   = crypt32.NewProc("CryptProtectData")
	pCryptUnprotectData = crypt32.NewProc("CryptUnprotectData")
	pLocalFree          = kernel32.NewProc("LocalFree")

	_ = gdi32 // reserved: the tray icon is loaded from file, not drawn
)

const (
	wsPopup     = 0x80000000
	wsExToolWin = 0x00000080
	wsExTopMost = 0x00000008

	swHide      = 0
	swShowNoAct = 4

	wmDestroy   = 0x0002
	wmClose     = 0x0010
	wmSize      = 0x0005
	wmCommand   = 0x0111
	wmApp       = 0x8000
	wmTray      = wmApp + 1
	wmSetSize   = wmApp + 2
	wmQuitApp   = wmApp + 3
	wmToggle    = wmApp + 4
	wmLButtonUp = 0x0202
	wmRButtonUp = 0x0205

	spiGetWorkArea = 0x0030

	hwndTopMost   = ^uintptr(0) // (HWND)-1
	swpNoActivate = 0x0010
	swpNoSize     = 0x0001
	swpNoMove     = 0x0002
	swpShowWindow = 0x0040

	nimAdd     = 0
	nimModify  = 1
	nimDelete  = 2
	nifMessage = 0x01
	nifIcon    = 0x02
	nifTip     = 0x04

	imageIcon      = 1
	lrLoadFromFile = 0x0010
	lrDefaultSize  = 0x0040

	mfString  = 0x0000
	tpmRight  = 0x0002
	tpmBottom = 0x0020

	idShow = 1001
	idQuit = 1002
)

type rectT struct{ Left, Top, Right, Bottom int32 }
type pointT struct{ X, Y int32 }

type msgT struct {
	Hwnd     windows.HWND
	Message  uint32
	WParam   uintptr
	LParam   uintptr
	Time     uint32
	Pt       pointT
	LPrivate uint32
}

type wndClassExW struct {
	Size, Style                      uint32
	WndProc                          uintptr
	ClsExtra, WndExtra               int32
	Instance, Icon, Cursor, Backgrnd windows.Handle
	MenuName, ClassName              *uint16
	IconSm                           windows.Handle
}

type notifyIconData struct {
	CbSize           uint32
	HWnd             windows.HWND
	UID              uint32
	UFlags           uint32
	UCallbackMessage uint32
	HIcon            windows.Handle
	SzTip            [128]uint16
	DwState          uint32
	DwStateMask      uint32
	SzInfo           [256]uint16
	UVersion         uint32
	SzInfoTitle      [64]uint16
	DwInfoFlags      uint32
	GuidItem         windows.GUID
	HBalloonIcon     windows.Handle
}

type accentPolicy struct {
	AccentState, AccentFlags, GradientColor, AnimationID uint32
}

type winCompAttrData struct {
	Attrib uintptr
	PvData unsafe.Pointer
	CbData uintptr
}

// ACCENT_ENABLE_TRANSPARENTGRADIENT. NOT the acrylic state (4): on Windows 10
// 19042 acrylic renders the window completely invisible here, and NOT
// WS_EX_LAYERED either — WebView2 refuses to create a controller on a layered
// parent and hands back a nil controller. Both verified on the target box.
const accentTransparentGradient = 2
const wcaAccentPolicy = 19

// setBackdrop paints the window's backdrop warm paper #F7F3EC at the given
// alpha. Content drawn by WebView2 on top stays fully opaque, so text is crisp
// over a see-through panel.
func setBackdrop(hwnd uintptr, alpha int) {
	// GradientColor is 0xAABBGGRR.
	color := uint32(alpha&0xFF)<<24 | 0x00ECF3F7
	ap := accentPolicy{AccentState: accentTransparentGradient, AccentFlags: 2, GradientColor: color}
	d := winCompAttrData{Attrib: wcaAccentPolicy, PvData: unsafe.Pointer(&ap), CbData: unsafe.Sizeof(ap)}
	_, _, _ = pSetWinCompAttr.Call(hwnd, uintptr(unsafe.Pointer(&d)))
}

func workArea() rectT {
	var wa rectT
	_, _, _ = pSysParamsInfoW.Call(spiGetWorkArea, 0, uintptr(unsafe.Pointer(&wa)), 0)
	return wa
}

func windowSize(hwnd uintptr) (int, int) {
	var r rectT
	_, _, _ = pGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&r)))
	return int(r.Right - r.Left), int(r.Bottom - r.Top)
}

func shellOpen(target string) {
	_, _, _ = pShellExecuteW.Call(0,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("open"))),
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr(target))), 0, 0, 1 /*SW_SHOWNORMAL*/)
}

// singleInstance returns false when another companion already owns the mutex.
func singleInstance() bool {
	h, _, err := pCreateMutexW.Call(0, 1,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("Global\\STLCompanionSingleton"))))
	if h == 0 {
		return true // cannot tell — better to run than to refuse
	}
	return !errors.Is(err, windows.ERROR_ALREADY_EXISTS)
}

// ---- DPAPI ----

type dataBlob struct {
	cbData uint32
	pbData *byte
}

func newBlob(b []byte) dataBlob {
	if len(b) == 0 {
		return dataBlob{}
	}
	return dataBlob{cbData: uint32(len(b)), pbData: &b[0]}
}

func (b dataBlob) bytes() []byte {
	if b.cbData == 0 || b.pbData == nil {
		return nil
	}
	return append([]byte(nil), unsafe.Slice(b.pbData, b.cbData)...)
}

func dpapiProtect(plain []byte) ([]byte, error) { return dpapiCall(pCryptProtectData, plain) }

func dpapiUnprotect(blob []byte) ([]byte, error) { return dpapiCall(pCryptUnprotectData, blob) }

// dpapiCall runs Crypt(Un)ProtectData with CRYPTPROTECT_UI_FORBIDDEN. Both
// functions share the same 7-argument shape; only the direction differs.
func dpapiCall(proc *windows.LazyProc, in []byte) ([]byte, error) {
	src := newBlob(in)
	var out dataBlob
	r, _, err := proc.Call(
		uintptr(unsafe.Pointer(&src)),
		0, 0, 0, 0,
		0x1, // CRYPTPROTECT_UI_FORBIDDEN: never block on a UI prompt
		uintptr(unsafe.Pointer(&out)),
	)
	if r == 0 {
		return nil, err
	}
	res := out.bytes()
	if out.pbData != nil {
		_, _, _ = pLocalFree.Call(uintptr(unsafe.Pointer(out.pbData)))
	}
	return res, nil
}
