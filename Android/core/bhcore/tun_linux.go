package bhcore

import (
	"errors"
	"unsafe"

	"golang.org/x/sys/unix"
)

// tunName — имя TUN-интерфейса по fd (Android/Linux: ioctl TUNGETIFF).
func tunName(fd int32) (string, error) {
	var ifr [unix.IFNAMSIZ + 64]byte
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(unix.TUNGETIFF), uintptr(unsafe.Pointer(&ifr[0])))
	if errno != 0 {
		return "", errno
	}
	return unix.ByteSliceToString(ifr[:]), nil
}

func findTunFD() (int32, error) { return -1, errors.New("TUN не открыт") }
