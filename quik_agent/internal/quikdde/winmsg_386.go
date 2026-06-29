//go:build windows && 386

package quikdde

// winMSG — MSG layout for Windows 32-bit, 28 bytes.
type winMSG struct {
	hwnd    uintptr
	message uint32
	wparam  uintptr
	lparam  uintptr
	time    uint32
	ptX     int32
	ptY     int32
}
