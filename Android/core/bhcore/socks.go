package bhcore

// Минимальный SOCKS5 (без пароля, только на 127.0.0.1): CONNECT и UDP ASSOCIATE.
// Все соединения уходят в туннель AmneziaWG через его сетевой стек.

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
)

const (
	socksVersion   = 5
	cmdConnect     = 1
	cmdUDPAssoc    = 3
	atypIPv4       = 1
	atypDomain     = 3
	atypIPv6       = 4
	repSuccess     = 0
	repFailure     = 1
	repCmdNotSupp  = 7
	udpIdleTimeout = 2 * time.Minute
)

func (t *awgTunnel) serve() {
	defer t.wg.Done()
	for {
		c, err := t.ln.Accept()
		if err != nil {
			return
		}
		go t.handle(c)
	}
}

func readAddr(r io.Reader, atyp byte) (string, error) {
	switch atyp {
	case atypIPv4:
		b := make([]byte, 4)
		if _, err := io.ReadFull(r, b); err != nil {
			return "", err
		}
		return netip.AddrFrom4([4]byte(b)).String(), nil
	case atypIPv6:
		b := make([]byte, 16)
		if _, err := io.ReadFull(r, b); err != nil {
			return "", err
		}
		return netip.AddrFrom16([16]byte(b)).String(), nil
	case atypDomain:
		n := make([]byte, 1)
		if _, err := io.ReadFull(r, n); err != nil {
			return "", err
		}
		b := make([]byte, n[0])
		if _, err := io.ReadFull(r, b); err != nil {
			return "", err
		}
		return string(b), nil
	}
	return "", errors.New("socks: неизвестный тип адреса")
}

func reply(c net.Conn, code byte, bind netip.AddrPort) {
	resp := []byte{socksVersion, code, 0}
	if !bind.IsValid() {
		bind = netip.AddrPortFrom(netip.IPv4Unspecified(), 0)
	}
	if bind.Addr().Is4() {
		a := bind.Addr().As4()
		resp = append(resp, atypIPv4)
		resp = append(resp, a[:]...)
	} else {
		a := bind.Addr().As16()
		resp = append(resp, atypIPv6)
		resp = append(resp, a[:]...)
	}
	resp = binary.BigEndian.AppendUint16(resp, bind.Port())
	_, _ = c.Write(resp)
}

func (t *awgTunnel) handle(c net.Conn) {
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(15 * time.Second))
	head := make([]byte, 2)
	if _, err := io.ReadFull(c, head); err != nil || head[0] != socksVersion {
		return
	}
	methods := make([]byte, head[1])
	if _, err := io.ReadFull(c, methods); err != nil {
		return
	}
	if _, err := c.Write([]byte{socksVersion, 0}); err != nil { // без аутентификации
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
	port := binary.BigEndian.Uint16(pb)
	switch req[1] {
	case cmdConnect:
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		remote, err := t.tnet.DialContext(ctx, "tcp", net.JoinHostPort(host, strconv.Itoa(int(port))))
		cancel()
		if err != nil {
			logf("awg: подключение к %s:%d: %v", host, port, err)
			reply(c, repFailure, netip.AddrPort{})
			return
		}
		defer remote.Close()
		reply(c, repSuccess, netip.AddrPortFrom(netip.IPv4Unspecified(), 0))
		_ = c.SetDeadline(time.Time{})
		pipe(c, remote)
	case cmdUDPAssoc:
		_ = c.SetDeadline(time.Time{})
		t.udpAssociate(c)
	default:
		reply(c, repCmdNotSupp, netip.AddrPort{})
	}
}

func pipe(a, b net.Conn) {
	var wg sync.WaitGroup
	wg.Add(2)
	cp := func(dst, src net.Conn) {
		defer wg.Done()
		_, _ = io.Copy(dst, src)
		if tc, ok := dst.(interface{ CloseWrite() error }); ok {
			_ = tc.CloseWrite()
		} else {
			_ = dst.Close()
		}
	}
	go cp(a, b)
	go cp(b, a)
	wg.Wait()
}

// udpAssociate: клиент шлёт датаграммы с заголовком SOCKS5 на наш UDP-порт,
// мы отправляем их в туннель и возвращаем ответы с тем же заголовком.
func (t *awgTunnel) udpAssociate(ctrl net.Conn) {
	local, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		reply(ctrl, repFailure, netip.AddrPort{})
		return
	}
	defer local.Close()
	remote, err := t.tnet.ListenUDPAddrPort(netip.AddrPortFrom(t.addr, 0))
	if err != nil {
		reply(ctrl, repFailure, netip.AddrPort{})
		return
	}
	defer remote.Close()
	reply(ctrl, repSuccess, local.LocalAddr().(*net.UDPAddr).AddrPort())

	var clientMu sync.Mutex
	var client *net.UDPAddr
	done := make(chan struct{})
	go func() { // соединение управления закрылось — ассоциация завершена
		_, _ = io.Copy(io.Discard, ctrl)
		close(done)
		_ = local.Close()
		_ = remote.Close()
	}()

	go func() { // ответы из туннеля → клиенту
		buf := make([]byte, 65535)
		for {
			_ = remote.SetReadDeadline(time.Now().Add(udpIdleTimeout))
			n, from, err := remote.ReadFrom(buf)
			if err != nil {
				_ = ctrl.Close()
				return
			}
			src, ok := from.(*net.UDPAddr)
			if !ok {
				continue
			}
			ap := src.AddrPort()
			hdr := []byte{0, 0, 0}
			if ap.Addr().Unmap().Is4() {
				a := ap.Addr().Unmap().As4()
				hdr = append(append(hdr, atypIPv4), a[:]...)
			} else {
				a := ap.Addr().As16()
				hdr = append(append(hdr, atypIPv6), a[:]...)
			}
			hdr = binary.BigEndian.AppendUint16(hdr, ap.Port())
			clientMu.Lock()
			to := client
			clientMu.Unlock()
			if to != nil {
				_, _ = local.WriteToUDP(append(hdr, buf[:n]...), to)
			}
		}
	}()

	buf := make([]byte, 65535)
	resolved := map[string]netip.Addr{}
	for {
		n, from, err := local.ReadFromUDP(buf)
		if err != nil {
			return
		}
		clientMu.Lock()
		client = from
		clientMu.Unlock()
		pkt := buf[:n]
		if len(pkt) < 4 || pkt[2] != 0 { // фрагментация не поддерживается
			continue
		}
		r := &sliceReader{b: pkt[4:]}
		host, err := readAddr(r, pkt[3])
		if err != nil || len(r.b) < 2 {
			continue
		}
		port := binary.BigEndian.Uint16(r.b[:2])
		payload := r.b[2:]
		addr, ok := resolved[host]
		if !ok {
			if a, err := netip.ParseAddr(host); err == nil {
				addr = a
			} else {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				ips, err := t.tnet.LookupContextHost(ctx, host)
				cancel()
				if err != nil || len(ips) == 0 {
					continue
				}
				if addr, err = netip.ParseAddr(ips[0]); err != nil {
					continue
				}
			}
			resolved[host] = addr
		}
		_, _ = remote.WriteTo(payload, net.UDPAddrFromAddrPort(netip.AddrPortFrom(addr, port)))
		select {
		case <-done:
			return
		default:
		}
	}
}

type sliceReader struct{ b []byte }

func (r *sliceReader) Read(p []byte) (int, error) {
	if len(r.b) == 0 {
		return 0, io.EOF
	}
	n := copy(p, r.b)
	r.b = r.b[n:]
	return n, nil
}
