//go:build windows && amd64

package quikdde

// winMSG — MSG layout for Windows x86-64, 48 bytes.
type winMSG struct {
	hwnd    uintptr
	message uint32
	_       uint32
	wparam  uintptr
	lparam  uintptr
	time    uint32
	ptX     int32
	ptY     int32
	_       [4]byte
}
