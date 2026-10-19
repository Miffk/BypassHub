//go:build !linux

package bhcore

import "net"

// writeFake: подмена через vmsplice есть только в Linux (Android) — здесь обычное перемешивание.
func writeFake(c *net.TCPConn, first, second, fake []byte) error {
	return writeDisorder(c, first, second)
}
