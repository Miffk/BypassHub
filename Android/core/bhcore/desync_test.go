package bhcore

import (
	"crypto/tls"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"testing"
	"time"
)

// socksDial — CONNECT через SOCKS5 на 127.0.0.1:port.
func socksDial(t *testing.T, port int, target string) net.Conn {
	c, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		t.Fatal(err)
	}
	host, ps, _ := net.SplitHostPort(target)
	ip := net.ParseIP(host).To4()
	var p int
	fmt.Sscan(ps, &p)
	req := []byte{5, 1, 0, 5, 1, 0, 1}
	req = append(req, ip...)
	req = binary.BigEndian.AppendUint16(req, uint16(p))
	c.Write(req)
	resp := make([]byte, 2+10)
	if _, err := io.ReadFull(c, resp); err != nil || resp[3] != 0 {
		t.Fatalf("SOCKS: %v %v", err, resp)
	}
	return c
}

func TestDesyncTLSHandshake(t *testing.T) {
	cert := selfSigned(t)
	ln, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{Certificates: []tls.Certificate{cert}})
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	sniSeen := make(chan string, 10)
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				tc := c.(*tls.Conn)
				if err := tc.Handshake(); err != nil {
					sniSeen <- "ERR " + err.Error()
					return
				}
				sniSeen <- tc.ConnectionState().ServerName
				buf := make([]byte, 1024)
				n, _ := tc.Read(buf)
				tc.Write(buf[:n])
			}(c)
		}
	}()
	for _, m := range []string{DesyncDisorder, DesyncRecordDisorder} {
		port, err := StartDesync(m)
		if err != nil {
			t.Fatal(err)
		}
		c := socksDial(t, port, ln.Addr().String())
		tc := tls.Client(c, &tls.Config{ServerName: "rr1---sn-abc.googlevideo.com", InsecureSkipVerify: true})
		tc.SetDeadline(time.Now().Add(5 * time.Second))
		if err := tc.Handshake(); err != nil {
			t.Fatalf("%s: рукопожатие: %v", m, err)
		}
		if got := <-sniSeen; got != "rr1---sn-abc.googlevideo.com" {
			t.Fatalf("%s: сервер увидел имя %q", m, got)
		}
		tc.Write([]byte("ping"))
		b := make([]byte, 4)
		if _, err := io.ReadFull(tc, b); err != nil || string(b) != "ping" {
			t.Fatalf("%s: эхо %q %v", m, b, err)
		}
		tc.Close()
		StopDesync()
	}
}

func TestSNIRangeAndRecordSplit(t *testing.T) {
	// настоящий ClientHello от crypto/tls
	a, b := net.Pipe()
	go func() {
		tls.Client(a, &tls.Config{ServerName: "www.youtube.com", InsecureSkipVerify: true}).Handshake()
	}()
	b.SetReadDeadline(time.Now().Add(2 * time.Second))
	hello, _ := readFirst(b)
	a.Close()
	start, n, ok := sniRange(hello)
	if !ok || string(hello[start:start+n]) != "www.youtube.com" {
		t.Fatalf("SNI не найден: %v", ok)
	}
	pos := start + n/2
	two := splitRecords(hello, pos)
	l1 := int(binary.BigEndian.Uint16(two[3:5]))
	l2 := int(binary.BigEndian.Uint16(two[5+l1+3:]))
	if len(two) != 10+l1+l2 || two[5+l1] != 0x16 {
		t.Fatal("записи разделены неверно")
	}
}
