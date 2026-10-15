package bhcore

import (
	"errors"
	"strings"

	"golang.org/x/sys/unix"
)

const sysprotoControl, utunOptIfname = 2, 2

// tunName — имя utun-интерфейса по fd (iOS/macOS: getsockopt UTUN_OPT_IFNAME).
func tunName(fd int32) (string, error) {
	return unix.GetsockoptString(int(fd), sysprotoControl, utunOptIfname)
}

// findTunFD — fd utun, который iOS открыла для расширения (так же делают sing-box и WireGuard).
func findTunFD() (int32, error) {
	for fd := 0; fd < 1024; fd++ {
		if name, err := unix.GetsockoptString(fd, sysprotoControl, utunOptIfname); err == nil &&
			strings.HasPrefix(name, "utun") {
			return int32(fd), nil
		}
	}
	return -1, errors.New("не найден utun расширения")
}
