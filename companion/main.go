// STLCompanion — портативный компаньон STL в трее Windows.
//
// Клик по иконке в трее открывает компактную панель поверх всех окон в правом
// нижнем углу, над часами: счёт, позиции, роботы, здоровье STL, эскалации.
// ✕ прячет обратно в трей, ⏻ закрывает приложение.
//
// Внутри — WebView2 без рамки; вёрстка приезжает из STL (companion.html), а exe
// отвечает только за окно, трей, токен и локальный прокси. Пароль не
// спрашивается: токен выдаётся один раз по коду сопряжения и шифруется DPAPI
// под учётную запись Windows.
//
// Собирать: CGO_ENABLED=0 go build -ldflags "-H=windowsgui" -o STLCompanion.exe ./companion
package main

import (
	_ "embed"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unsafe"

	"github.com/jchv/go-webview2/pkg/edge"
	"golang.org/x/sys/windows"
)

//go:embed tray.ico
var trayIcon []byte

const (
	className  = "STLCompanionWindow"
	windowName = "STL Companion"
	marginPx   = 8 // отступ от края рабочей области
	minHeight  = 120
	// Сколько пикселей панели обязано остаться на экране при перетаскивании:
	// утащить её целиком за край нельзя, иначе вернуть можно было бы только
	// правкой config.json руками.
	dragKeepVisible = 48
)

type app struct {
	cfg     config
	lnk     *link
	hwnd    uintptr
	browser *edge.Chromium
	nid     notifyIconData
	height  int
	width   int    // текущая ширина окна; страница может её менять (см. /width)
	base    string // адрес локального прокси: нужен, чтобы перечитать страницу
}

var a app

func main() {
	if !singleInstance() {
		return // компаньон уже запущен — второй в трее не нужен
	}
	_, _, _ = pSetProcessDPI.Call()

	a.cfg = loadConfig()
	a.lnk = newLink(a.cfg)
	base, err := a.lnk.serve()
	if err != nil {
		fatal("не удалось поднять локальный порт: " + err.Error())
		return
	}
	// Кнопки панели дёргают прокси, а он — оконный поток: любое действие с окном
	// обязано ехать сообщением, иначе Win32 вызовут из чужой горутины.
	a.lnk.onHide = func() { post(wmToggle, 0) }
	a.lnk.onQuit = func() { post(wmQuitApp, 0) }
	a.lnk.onHeight = func(h int) { post(wmSetSize, uintptr(h)) }
	a.lnk.onWidth = func(w int) { post(wmSetWidth, uintptr(w)) }
	a.lnk.onClip = func(s string) error { return setClipboard(a.hwnd, s) }
	a.lnk.onMove = func(dx, dy int) { post2(wmMoveBy, packInt(dx), packInt(dy)) }
	a.lnk.onMoveDone = func() { post(wmMoveSave, 0) }

	a.base = base
	a.width = a.cfg.Width
	a.height = 320
	createWindow()
	setBackdrop(a.hwnd, a.cfg.BGAlpha)
	startBrowser(base)
	addTrayIcon()
	showPanel()

	var m msgT
	for {
		r, _, _ := pGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(r) <= 0 {
			break
		}
		_, _, _ = pTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
		_, _, _ = pDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
	}
	_, _, _ = pShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&a.nid)))
}

func post(msg uint32, wparam uintptr) {
	_, _, _ = pPostMessageW.Call(a.hwnd, uintptr(msg), wparam, 0)
}

func post2(msg uint32, wparam, lparam uintptr) {
	_, _, _ = pPostMessageW.Call(a.hwnd, uintptr(msg), wparam, lparam)
}

// packInt/unpackInt проносят ЗНАКОВОЕ смещение через беззнаковые параметры
// сообщения: сдвиг влево и вверх отрицателен, а uintptr на 64 битах превратил бы
// -1 в четыре миллиарда.
func packInt(v int) uintptr { return uintptr(uint32(int32(v))) }

func unpackInt(v uintptr) int { return int(int32(uint32(v))) }

func fatal(text string) {
	_, _, _ = user32.NewProc("MessageBoxW").Call(0,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr(text))),
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("STL Companion"))), 0x10)
}

// ---- окно ----

func createWindow() {
	inst, _, _ := pGetModuleHandleW.Call(0)
	cls := windows.StringToUTF16Ptr(className)
	cur, _, _ := pLoadCursorW.Call(0, 32512) // IDC_ARROW
	icon := loadTrayIcon()

	wc := wndClassExW{
		Size:      uint32(unsafe.Sizeof(wndClassExW{})),
		WndProc:   windows.NewCallback(wndProc),
		Instance:  windows.Handle(inst),
		Icon:      windows.Handle(icon),
		Cursor:    windows.Handle(cur),
		ClassName: cls,
	}
	if r, _, err := pRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc))); r == 0 {
		fatal("RegisterClassEx: " + err.Error())
		os.Exit(1)
	}

	x, y := origin()
	// Без WS_EX_LAYERED: WebView2 не создаёт контроллер в layered-окне.
	// WS_EX_TOOLWINDOW убирает кнопку с панели задач и Alt+Tab.
	hw, _, err := pCreateWindowExW.Call(
		wsExToolWin|wsExTopMost,
		uintptr(unsafe.Pointer(cls)),
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr(windowName))),
		wsPopup, uintptr(x), uintptr(y), uintptr(a.cfg.Width), uintptr(a.height),
		0, 0, inst, 0)
	if hw == 0 {
		fatal("CreateWindowEx: " + err.Error())
		os.Exit(1)
	}
	a.hwnd = hw
}

// panelOrigin ставит панель в правый нижний угол РАБОЧЕЙ области, то есть над
// часами и датой, а не под панелью задач.
func panelOrigin(w, h int) (int, int) {
	wa := workArea()
	return int(wa.Right) - w - marginPx, int(wa.Bottom) - h - marginPx
}

// origin — где панель стоит сейчас: в углу по умолчанию либо там, куда её
// перетащил оператор. Сохранённая позиция всегда проходит через зажим: монитор
// могли отключить, а разрешение сменить, и панель оказалась бы вне экрана.
func origin() (int, int) {
	if !a.cfg.Placed {
		return panelOrigin(a.width, a.height)
	}
	return clampOrigin(a.cfg.PosX, a.cfg.PosY, a.width, a.height)
}

// clampOrigin не пускает панель за пределы всех мониторов. По горизонтали
// достаточно оставить видимой полоску, по вертикали ВЕРХ обязан быть на экране:
// тащат за верхнюю строку, и уехавшую вверх панель было бы не ухватить.
func clampOrigin(x, y, w, h int) (int, int) {
	return clampIn(virtualScreen(), x, y, w, h)
}

func clampIn(vs rectT, x, y, w, h int) (int, int) {
	x = clampInt(x, int(vs.Left)-w+dragKeepVisible, int(vs.Right)-dragKeepVisible)
	y = clampInt(y, int(vs.Top), int(vs.Bottom)-dragKeepVisible)
	return x, y
}

func clampInt(v, lo, hi int) int {
	if hi < lo {
		return lo
	}
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// moveBy сдвигает окно на дельту, присланную страницей во время перетаскивания.
// Дельты ИНКРЕМЕНТНЫЕ: авторитет положения — само окно, поэтому пропущенное
// сообщение не копит ошибку.
func moveBy(dx, dy int) {
	x, y := windowOrigin(a.hwnd)
	a.cfg.PosX, a.cfg.PosY = clampOrigin(x+dx, y+dy, a.width, a.height)
	a.cfg.Placed = true
	_, _, _ = pSetWindowPos.Call(a.hwnd, hwndTopMost,
		uintptr(a.cfg.PosX), uintptr(a.cfg.PosY), 0, 0, swpNoActivate|swpNoSize)
}

// reloadPage перечитывает вёрстку панели. Страница приезжает из STL и берётся
// прокси ТОЛЬКО при навигации, поэтому раньше каждая правка вёрстки требовала
// перезапуска exe — за один день это случилось трижды. К адресу цепляем метку
// времени: перезагрузка по тому же URL может отдаться из кэша WebView2.
func reloadPage() {
	if a.browser == nil || a.base == "" {
		return
	}
	sep := "?"
	if strings.Contains(a.base, "?") {
		sep = "&"
	}
	a.browser.Navigate(a.base + sep + "r=" + strconv.FormatInt(time.Now().UnixMilli(), 10))
}

// resetPos возвращает панель в правый нижний угол. Страховка на случай, когда
// оператор утащил её на монитор, которого больше нет.
func resetPos() {
	a.cfg.Placed = false
	applyBounds()
	_ = a.cfg.save()
}

func resizePanel(h int) {
	if h < minHeight {
		h = minHeight
	}
	// Зажим по ТОМУ монитору, где окно сейчас, а не по главному: панель таскают
	// между экранами, и на 4К-портрете рядом с обычным монитором зажим по
	// главному резал высоту втрое.
	wa := workAreaOf(a.hwnd)
	if max := int(wa.Bottom-wa.Top) - 2*marginPx; h > max {
		h = max
	}
	if h == a.height {
		return
	}
	a.height = h
	applyBounds()
}

// resizeWidth меняет ширину окна по запросу страницы. Так «увеличить размер»
// работает независимо от config.json на машине оператора: авторитет размера —
// страница, а не файл, который правится руками.
func resizeWidth(w int) {
	if w < 260 {
		w = 260
	}
	wa := workAreaOf(a.hwnd)
	if max := int(wa.Right-wa.Left) - 2*marginPx; w > max {
		w = max
	}
	if w == a.width {
		return
	}
	a.width = w
	applyBounds()
}

func applyBounds() {
	x, y := origin()
	_, _, _ = pSetWindowPos.Call(a.hwnd, hwndTopMost, uintptr(x), uintptr(y),
		uintptr(a.width), uintptr(a.height), swpNoActivate)
}

func panelVisible() bool {
	v, _, _ := pIsWindowVisible.Call(a.hwnd)
	return v != 0
}

func showPanel() {
	// Пересчитываем позицию: панель задач могла переехать, разрешение измениться.
	x, y := origin()
	_, _, _ = pSetWindowPos.Call(a.hwnd, hwndTopMost, uintptr(x), uintptr(y),
		uintptr(a.width), uintptr(a.height), swpShowWindow|swpNoActivate)
	_, _, _ = pSetForegroundWin.Call(a.hwnd)
}

func hidePanel() { _, _, _ = pShowWindow.Call(a.hwnd, swHide) }

func togglePanel() {
	if panelVisible() {
		hidePanel()
		return
	}
	showPanel()
}

func wndProc(hwnd uintptr, msg uint32, wparam, lparam uintptr) uintptr {
	switch msg {
	case wmSize:
		if a.browser != nil {
			a.browser.Resize()
		}
	case wmSetSize:
		resizePanel(int(wparam))
	case wmSetWidth:
		resizeWidth(int(wparam))
	case wmMoveBy:
		moveBy(unpackInt(wparam), unpackInt(lparam))
	case wmMoveSave:
		_ = a.cfg.save()
	case wmMoveReset:
		resetPos()
	case wmReload:
		reloadPage()
	case wmToggle:
		togglePanel()
	case wmQuitApp:
		quit()
		return 0
	case wmTray:
		switch lparam {
		case wmLButtonUp:
			togglePanel()
		case wmRButtonUp:
			trayMenu()
		}
		return 0
	case wmCommand:
		switch wparam & 0xFFFF {
		case idShow:
			showPanel()
		case idReload:
			reloadPage()
			showPanel()
		case idReset:
			resetPos()
			showPanel()
		case idQuit:
			quit()
		}
		return 0
	case wmClose:
		// Крестик окна прячет в трей, а не закрывает: закрытие — только ⏻.
		hidePanel()
		return 0
	case wmDestroy:
		_, _, _ = pPostQuitMessage.Call(0)
		return 0
	}
	r, _, _ := pDefWindowProcW.Call(hwnd, uintptr(msg), wparam, lparam)
	return r
}

func quit() {
	_, _, _ = pShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&a.nid)))
	_, _, _ = pPostQuitMessage.Call(0)
}

// ---- WebView2 ----

func startBrowser(url string) {
	a.browser = edge.NewChromium()
	a.browser.DataPath = filepath.Join(configDir(), "webview")
	if !a.browser.Embed(a.hwnd) {
		fatal("Не удалось запустить WebView2. Установите Microsoft Edge WebView2 Runtime.")
		os.Exit(1)
	}
	// Прозрачный фон вебвью: тёплую подложку рисует композитор Windows.
	if c2 := a.browser.GetController().GetICoreWebView2Controller2(); c2 != nil {
		_ = c2.PutDefaultBackgroundColor(edge.COREWEBVIEW2_COLOR{A: 0})
	}
	_ = a.browser.Show()
	a.browser.Resize()
	a.browser.Navigate(url)
}

// ---- трей ----

// loadTrayIcon пишет вшитую иконку рядом с настройками и грузит её с диска:
// ресурсной секции в exe нет, а тащить ради неё генератор ресурсов незачем.
func loadTrayIcon() uintptr {
	path := filepath.Join(configDir(), "tray.ico")
	if err := os.MkdirAll(configDir(), 0o700); err == nil {
		if cur, err := os.ReadFile(path); err != nil || len(cur) != len(trayIcon) {
			_ = os.WriteFile(path, trayIcon, 0o600)
		}
	}
	h, _, _ := pLoadImageW.Call(0,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr(path))),
		imageIcon, 16, 16, lrLoadFromFile|lrDefaultSize)
	if h == 0 {
		h, _, _ = pLoadIconW.Call(0, 32512) // IDI_APPLICATION — лучше, чем пусто
	}
	return h
}

func addTrayIcon() {
	a.nid = notifyIconData{
		CbSize:           uint32(unsafe.Sizeof(notifyIconData{})),
		HWnd:             windows.HWND(a.hwnd),
		UID:              1,
		UFlags:           nifMessage | nifIcon | nifTip,
		UCallbackMessage: wmTray,
		HIcon:            windows.Handle(loadTrayIcon()),
	}
	copy(a.nid.SzTip[:], windows.StringToUTF16("STL Companion"))
	if r, _, _ := pShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&a.nid))); r == 0 {
		_, _, _ = pShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&a.nid)))
	}
}

func trayMenu() {
	menu, _, _ := pCreatePopupMenu.Call()
	if menu == 0 {
		return
	}
	defer func() { _, _, _ = pDestroyMenu.Call(menu) }()
	_, _, _ = pAppendMenuW.Call(menu, mfString, idShow,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("Показать панель"))))
	_, _, _ = pAppendMenuW.Call(menu, mfString, idReload,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("Обновить панель"))))
	_, _, _ = pAppendMenuW.Call(menu, mfString, idReset,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("Вернуть в правый нижний угол"))))
	_, _, _ = pAppendMenuW.Call(menu, mfString, idQuit,
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("Выход"))))
	var pt pointT
	_, _, _ = pGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
	// Меню закрывается по клику мимо только если окно на переднем плане.
	_, _, _ = pSetForegroundWin.Call(a.hwnd)
	_, _, _ = pTrackPopupMenu.Call(menu, tpmRight|tpmBottom,
		uintptr(pt.X), uintptr(pt.Y), 0, a.hwnd, 0)
}
