package bhcore

// Обход DPI для TCP без root — приём «disorder» из ByeDPI/zapret.
//
// Локальный SOCKS5 (127.0.0.1): sing-box отправляет в него соединения к сайтам из списков обхода.
// Начало TLS-соединения (ClientHello) делится посередине имени сайта, и первая часть уходит с TTL=1:
// она пропадает на первом же узле сети, а фильтр провайдера сначала видит вторую часть — без начала
// имени. Ядро телефона тут же повторяет первую часть уже с обычным TTL, и сервер собирает всё целиком,
// а фильтр, который не умеет собирать пакеты не по порядку, имя сайта так и не видит.
// Вариант с TLS-записями дополнительно делит ClientHello на две корректные TLS-записи.
//
// Фильтры, которые умеют собирать части по порядку (так бывает в мобильных сетях), это не обманывает.
// Для них два других приёма:
//   - fake (как --fake в ByeDPI, только Android/Linux): сначала уходит подставная первая часть с малым TTL —
//     она доходит до фильтра, но не до сайта, и фильтр принимает её за начало соединения с другим именем.
//     Ядро телефона потом повторяет эту часть уже настоящей (подмена в той же странице памяти — desync_fake_linux.go);
//   - oob (как --oob в ByeDPI): в середину имени сайта вставляется один байт срочных данных (TCP URG).
//     Сайт его выбрасывает, а фильтр видит искажённое имя.

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/netip"
	"strconv"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

const (
	DesyncDisorder       = "disorder"        // TCP: первая часть с TTL=1
	DesyncRecordDisorder = "record-disorder" // TLS-записи + TCP disorder
	DesyncFake           = "fake"            // подставная первая часть с малым TTL
	DesyncOOB            = "oob"             // байт срочных данных в середине имени сайта

	desyncFakeTTL = 5 // дальше фильтра провайдера, но ближе сайта
)

// IsDesyncMethod — способ обхода, который делает это ядро (а не sing-box).
func IsDesyncMethod(method string) bool {
	switch method {
	case DesyncDisorder, DesyncRecordDisorder, DesyncFake, DesyncOOB:
		return true
	}
	return false
}

type desyncServer struct {
	ln     net.Listener
	method string
	wg     sync.WaitGroup
	mu     sync.Mutex
	conns  map[net.Conn]struct{}
	closed bool
}

var (
	desyncMu  sync.Mutex
	desyncCur *desyncServer
)

// StartDesync запускает локальный SOCKS5 с приёмом обхода method и возвращает его порт.
func StartDesync(method string) (int, error) {
	if !IsDesyncMethod(method) {
		return 0, errors.New("неизвестный способ обхода: " + method)
	}
	StopDesync()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	s := &desyncServer{ln: ln, method: method, conns: map[net.Conn]struct{}{}}
	desyncMu.Lock()
	desyncCur = s
	desyncMu.Unlock()
	s.wg.Add(1)
	go s.serve()
	return ln.Addr().(*net.TCPAddr).Port, nil
}

// StopDesync останавливает сервер и закрывает соединения.
func StopDesync() {
	desyncMu.Lock()
	s := desyncCur
	desyncCur = nil
	desyncMu.Unlock()
	if s == nil {
		return
	}
	s.mu.Lock()
	s.closed = true
	for c := range s.conns {
		_ = c.Close()
	}
	s.mu.Unlock()
	_ = s.ln.Close()
	s.wg.Wait()
}

func (s *desyncServer) track(c net.Conn, add bool) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if add {
		if s.closed {
			return false
		}
		s.conns[c] = struct{}{}
	} else {
		delete(s.conns, c)
	}
	return true
}

func (s *desyncServer) serve() {
	defer s.wg.Done()
	for {
		c, err := s.ln.Accept()
		if err != nil {
			return
		}
		go s.handle(c)
	}
}

func (s *desyncServer) handle(c net.Conn) {
	if !s.track(c, true) {
		c.Close()
		return
	}
	defer func() {
		s.track(c, false)
		c.Close()
	}()
	_ = c.SetDeadline(time.Now().Add(15 * time.Second))
	head := make([]byte, 2)
	if _, err := io.ReadFull(c, head); err != nil || head[0] != socksVersion {
		return
	}
	if _, err := io.ReadFull(c, make([]byte, head[1])); err != nil {
		return
	}
	if _, err := c.Write([]byte{socksVersion, 0}); err != nil {
		return
	}
	req := make([]byte, 4)
	if _, err := io.ReadFull(c, req); err != nil {
		return
	}
	host, err := readAddr(c, req[3])
	if err != nil {
		return
	}
	pb := make([]byte, 2)
	if _, err := io.ReadFull(c, pb); err != nil {
		return
	}
	if req[1] != cmdConnect {
		reply(c, repCmdNotSupp, netip.AddrPort{})
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	remote, err := (&net.Dialer{}).DialContext(ctx, "tcp", net.JoinHostPort(host, strconv.Itoa(int(binary.BigEndian.Uint16(pb)))))
	cancel()
	if err != nil {
		reply(c, repFailure, netip.AddrPort{})
		return
	}
	if !s.track(remote, true) {
		remote.Close()
		return
	}
	defer func() {
		s.track(remote, false)
		remote.Close()
	}()
	reply(c, repSuccess, netip.AddrPortFrom(netip.IPv4Unspecified(), 0))

	// начало соединения: ждём ClientHello целиком (обычно приходит сразу)
	_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
	first, err := readFirst(c)
	_ = c.SetDeadline(time.Time{})
	if len(first) > 0 {
		if werr := s.sendFirst(remote.(*net.TCPConn), first); werr != nil {
			return
		}
	}
	if err != nil && !isTimeout(err) {
		return
	}
	pipe(c, remote)
}

func isTimeout(err error) bool {
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout()
}

// readFirst читает первую TLS-запись целиком (или то, что пришло, если это не TLS).
func readFirst(c net.Conn) ([]byte, error) {
	buf := make([]byte, 0, 2048)
	tmp := make([]byte, 16*1024)
	for {
		n, err := c.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			return buf, err
		}
		if len(buf) >= 5 {
			if buf[0] != 0x16 {
				return buf, nil
			}
			if need := 5 + int(binary.BigEndian.Uint16(buf[3:5])); len(buf) >= need {
				return buf, nil
			}
		}
	}
}

// sniRange — где в ClientHello (вместе с заголовком TLS-записи) лежит имя сайта.
func sniRange(b []byte) (int, int, bool) {
	if len(b) < 5+4 || b[0] != 0x16 || b[5] != 0x01 {
		return 0, 0, false
	}
	p := 5 + 4 + 2 + 32 // заголовки, версия, random
	if p >= len(b) {
		return 0, 0, false
	}
	p += 1 + int(b[p]) // session id
	if p+2 > len(b) {
		return 0, 0, false
	}
	p += 2 + int(binary.BigEndian.Uint16(b[p:])) // cipher suites
	if p >= len(b) {
		return 0, 0, false
	}
	p += 1 + int(b[p]) // compression
	if p+2 > len(b) {
		return 0, 0, false
	}
	end := p + 2 + int(binary.BigEndian.Uint16(b[p:]))
	p += 2
	if end > len(b) {
		end = len(b)
	}
	for p+4 <= end {
		typ := binary.BigEndian.Uint16(b[p:])
		ln := int(binary.BigEndian.Uint16(b[p+2:]))
		body := p + 4
		if typ == 0 && body+5 <= end {
			nameLen := int(binary.BigEndian.Uint16(b[body+3:]))
			start := body + 5
			if start+nameLen <= len(b) && nameLen > 0 {
				return start, nameLen, true
			}
			return 0, 0, false
		}
		p = body + ln
	}
	return 0, 0, false
}

// splitRecords делит одну TLS-запись на две в позиции pos (pos — внутри тела записи).
func splitRecords(b []byte, pos int) []byte {
	out := make([]byte, 0, len(b)+5)
	out = append(out, b[0], b[1], b[2])
	out = binary.BigEndian.AppendUint16(out, uint16(pos-5))
	out = append(out, b[5:pos]...)
	out = append(out, b[0], b[1], b[2])
	out = binary.BigEndian.AppendUint16(out, uint16(len(b)-pos))
	out = append(out, b[pos:]...)
	return out
}

func (s *desyncServer) sendFirst(remote *net.TCPConn, data []byte) error {
	start, n, ok := sniRange(data)
	if !ok || len(data) < 5+int(binary.BigEndian.Uint16(data[3:5])) {
		_, err := remote.Write(data)
		return err
	}
	pos := start + n/2
	if pos <= 5 {
		pos = start + 1
	}
	recEnd := 5 + int(binary.BigEndian.Uint16(data[3:5]))
	rest := data[recEnd:]
	data = data[:recEnd]
	if s.method == DesyncRecordDisorder {
		// две TLS-записи; TCP режем по их границе: первая часть — первая запись целиком
		data = splitRecords(data, pos)
	}
	cut := pos
	var err error
	switch s.method {
	case DesyncFake:
		err = writeFake(remote, data[:cut], data[cut:], fakePart(data[:cut], start))
	case DesyncOOB:
		err = writeOOB(remote, data[:cut], data[cut:])
	default:
		err = writeDisorder(remote, data[:cut], data[cut:])
	}
	if err != nil {
		return err
	}
	if len(rest) > 0 {
		_, err := remote.Write(rest)
		return err
	}
	return nil
}

// fakePart — подставная первая часть той же длины: тот же ClientHello, но начало имени сайта заменено.
func fakePart(first []byte, sniStart int) []byte {
	fake := append([]byte(nil), first...)
	const letters = "abcdefghijklmnopqrstuvwxyz"
	for i := sniStart; i < len(fake); i++ {
		if fake[i] != '.' {
			fake[i] = letters[(i-sniStart)%len(letters)]
		}
	}
	return fake
}

// ttlOption — опция TTL сокета (IPv4 или IPv6).
func ttlOption(c *net.TCPConn) (int, int) {
	if a, ok := c.RemoteAddr().(*net.TCPAddr); ok && a.IP.To4() == nil {
		return unix.IPPROTO_IPV6, unix.IPV6_UNICAST_HOPS
	}
	return unix.IPPROTO_IP, unix.IP_TTL
}

// writeOOB: первая часть и за ней один байт срочных данных (MSG_OOB), затем вторая часть.
// Сайт срочный байт в поток не берёт, а фильтр провайдера видит его посреди имени сайта.
func writeOOB(c *net.TCPConn, first, second []byte) error {
	rc, err := c.SyscallConn()
	if err != nil {
		return err
	}
	buf := append(append([]byte(nil), first...), 'a')
	var serr error
	if err := rc.Write(func(fd uintptr) bool {
		var n int
		n, serr = unix.SendmsgN(int(fd), buf, nil, nil, unix.MSG_OOB)
		if serr == unix.EAGAIN {
			return false
		}
		if serr == nil && n != len(buf) {
			serr = io.ErrShortWrite
		}
		return true
	}); err != nil {
		return err
	}
	if serr != nil {
		return serr
	}
	_, err = c.Write(second)
	return err
}

// writeDisorder: первая часть с TTL=1 (теряется до фильтра провайдера и повторяется ядром),
// вторая — с обычным TTL.
func writeDisorder(c *net.TCPConn, first, second []byte) error {
	rc, err := c.SyscallConn()
	if err != nil {
		return err
	}
	level, opt := ttlOption(c)
	orig := 64
	_ = rc.Control(func(fd uintptr) {
		if v, err := unix.GetsockoptInt(int(fd), level, opt); err == nil && v > 0 {
			orig = v
		}
	})
	setTTL := func(ttl int) error {
		var serr error
		if err := rc.Control(func(fd uintptr) { serr = unix.SetsockoptInt(int(fd), level, opt, ttl) }); err != nil {
			return err
		}
		return serr
	}
	if err := setTTL(1); err != nil {
		// не удалось — отправляем двумя частями без TTL-приёма
		if _, err := c.Write(first); err != nil {
			return err
		}
		_, err := c.Write(second)
		return err
	}
	_, werr := c.Write(first)
	if err := setTTL(orig); err != nil && werr == nil {
		werr = err
	}
	if werr != nil {
		return werr
	}
	_, err = c.Write(second)
	return err
}
