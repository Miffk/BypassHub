package bhcore

import (
	"errors"
	"net"
	"os"
	"time"

	"golang.org/x/sys/unix"
)

// writeFake — приём fake из ByeDPI без root. Первая часть уходит из страницы памяти, отданной ядру через
// vmsplice/splice без копирования: сначала там лежит подставная часть, и она отправляется с малым TTL —
// фильтр провайдера её видит, а до сайта она не доходит. Сразу после отправки страница заполняется
// настоящими данными и TTL возвращается: подтверждения от сайта нет, и ядро повторяет часть уже настоящей.
func writeFake(c *net.TCPConn, first, second, fake []byte) error {
	ok, err := sendFake(c, first, fake)
	if err != nil {
		return err
	}
	if !ok {
		// приём недоступен (не та система, мало памяти) — обычное перемешивание
		return writeDisorder(c, first, second)
	}
	_, err = c.Write(second)
	return err
}

// sendFake: false без ошибки — ничего не отправлено, можно попробовать другой приём.
func sendFake(c *net.TCPConn, real, fake []byte) (bool, error) {
	if len(real) == 0 || len(real) > os.Getpagesize() || len(fake) != len(real) {
		return false, nil
	}
	mem, err := unix.Mmap(-1, 0, os.Getpagesize(), unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED|unix.MAP_ANON)
	if err != nil {
		return false, nil
	}
	defer unix.Munmap(mem)
	var p [2]int
	if err := unix.Pipe2(p[:], unix.O_CLOEXEC); err != nil {
		return false, nil
	}
	defer unix.Close(p[0])
	defer unix.Close(p[1])
	copy(mem, fake)

	rc, err := c.SyscallConn()
	if err != nil {
		return false, err
	}
	level, opt := ttlOption(c)
	sent := false
	var serr error
	if err := rc.Control(func(fd uintptr) {
		s := int(fd)
		orig, err := unix.GetsockoptInt(s, level, opt)
		if err != nil || orig <= 0 {
			orig = 64
		}
		if unix.SetsockoptInt(s, level, opt, desyncFakeTTL) != nil {
			return
		}
		defer unix.SetsockoptInt(s, level, opt, orig)
		n, err := unix.Vmsplice(p[1], []unix.Iovec{iovec(mem[:len(real)])}, unix.SPLICE_F_GIFT)
		if err != nil || n != len(real) {
			return // в соединение ещё ничего не ушло
		}
		m, err := unix.Splice(p[0], nil, s, nil, len(real), 0)
		if err != nil || int(m) != len(real) {
			// часть подставных данных уже в соединении — его не спасти
			serr = errFakeBroken
			return
		}
		waitSent(s)
		copy(mem, real)
		sent = true
	}); err != nil {
		return false, err
	}
	return sent, serr
}

var errFakeBroken = errors.New("подставная часть ушла не целиком")

func iovec(b []byte) unix.Iovec {
	v := unix.Iovec{Base: &b[0]}
	v.SetLen(len(b))
	return v
}

// waitSent ждёт, пока ядро отправит всё из очереди сокета (не дольше ~50 мс), — только после этого
// страницу можно заполнить настоящими данными.
func waitSent(fd int) {
	for i := 0; i < 50; i++ {
		info, err := unix.GetsockoptTCPInfo(fd, unix.IPPROTO_TCP, unix.TCP_INFO)
		if err != nil || info.Notsent_bytes == 0 {
			return
		}
		time.Sleep(time.Millisecond)
	}
}
