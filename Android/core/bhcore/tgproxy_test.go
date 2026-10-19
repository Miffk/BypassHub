package bhcore

import (
	"bufio"
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/hex"
	"io"
	"math/big"
	"net"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
)

// Имитация Telegram: обычная обфускация на стороне сервера, эхо расшифрованных пакетов.
type tgServerSide struct {
	dec, enc interface{ XORKeyStream(dst, src []byte) }
	dc       int16
	tag      []byte
}

func newServerSide(init []byte) *tgServerSide {
	s := &tgServerSide{}
	s.dec = tgCTR(init[8:40], init[40:56])
	plain := make([]byte, 64)
	s.dec.XORKeyStream(plain, init)
	s.tag = plain[56:60]
	s.dc = int16(binary.LittleEndian.Uint16(plain[60:62]))
	rev := tgReverse(init[8:56])
	s.enc = tgCTR(rev[:32], rev[32:48])
	return s
}

// Клиент Telegram с секретом dd: рукопожатие и шифры.
type tgClientSide struct {
	init     []byte
	enc, dec interface{ XORKeyStream(dst, src []byte) }
}

func newClientSide(secret []byte, dc int16, tag []byte) *tgClientSide {
	rnd := make([]byte, 64)
	for {
		rand.Read(rnd)
		if rnd[0] != 0xef && !bytes.Equal(rnd[4:8], []byte{0, 0, 0, 0}) {
			break
		}
	}
	copy(rnd[56:60], tag)
	binary.LittleEndian.PutUint16(rnd[60:62], uint16(dc))
	c := &tgClientSide{}
	c.enc = tgCTR(tgKey(rnd[8:40], secret), rnd[40:56])
	enc := make([]byte, 64)
	c.enc.XORKeyStream(enc, rnd)
	c.init = append(append([]byte{}, rnd[:56]...), enc[56:]...)
	rev := tgReverse(rnd[8:56])
	c.dec = tgCTR(tgKey(rev[:32], secret), rev[32:48])
	return c
}

func selfSigned(t *testing.T) tls.Certificate {
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tmpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), DNSNames: []string{"kws2.web.telegram.org"}}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
}

func packet(n int, fill byte) []byte { // padded intermediate: длина LE + данные
	b := make([]byte, 4+n)
	binary.LittleEndian.PutUint32(b, uint32(n))
	for i := 4; i < len(b); i++ {
		b[i] = fill
	}
	return b
}

func TestTGProxyWebSocketAndTCP(t *testing.T) {
	// --- сервер WebSocket (как kws2.web.telegram.org)
	cert := selfSigned(t)
	wsLn, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{Certificates: []tls.Certificate{cert}})
	if err != nil {
		t.Fatal(err)
	}
	defer wsLn.Close()
	var mu sync.Mutex
	var frames []int
	var hosts []string
	go func() {
		for {
			c, err := wsLn.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				br := bufio.NewReader(c)
				req, err := http.ReadRequest(br)
				if err != nil || req.URL.Path != "/apiws" {
					return
				}
				mu.Lock()
				hosts = append(hosts, req.Host)
				mu.Unlock()
				c.Write([]byte("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"))
				ws := &tgWS{conn: c, br: br}
				init, err := ws.recv()
				if err != nil || len(init) != 64 {
					return
				}
				s := newServerSide(init)
				if s.dc != 2 || !bytes.Equal(s.tag, tgTagSecure) {
					t.Errorf("рукопожатие к Telegram: DC%d тег %x", s.dc, s.tag)
					return
				}
				for {
					msg, err := ws.recv()
					if err != nil {
						return
					}
					mu.Lock()
					frames = append(frames, len(msg))
					mu.Unlock()
					plain := make([]byte, len(msg))
					s.dec.XORKeyStream(plain, msg)
					out := make([]byte, len(plain))
					s.enc.XORKeyStream(out, plain)
					// ответ — без маски, одним кадром
					hdr := []byte{0x82}
					if len(out) < 126 {
						hdr = append(hdr, byte(len(out)))
					} else {
						hdr = append(hdr, 126, byte(len(out)>>8), byte(len(out)))
					}
					c.Write(append(hdr, out...))
				}
			}(c)
		}
	}()
	// --- сервер TCP (прямое подключение к DC5)
	tcpLn, _ := net.Listen("tcp", "127.0.0.1:0")
	defer tcpLn.Close()
	go func() {
		for {
			c, err := tcpLn.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				init := make([]byte, 64)
				if _, err := io.ReadFull(c, init); err != nil {
					return
				}
				s := newServerSide(init)
				buf := make([]byte, 65536)
				for {
					n, err := c.Read(buf)
					if n > 0 {
						s.dec.XORKeyStream(buf[:n], buf[:n])
						s.enc.XORKeyStream(buf[:n], buf[:n])
						c.Write(buf[:n])
					}
					if err != nil {
						return
					}
				}
			}(c)
		}
	}()

	_, wsPort, _ := net.SplitHostPort(wsLn.Addr().String())
	_, tcpPort, _ := net.SplitHostPort(tcpLn.Addr().String())
	oldRedir, oldIPs, oldWS, oldTCP := tgWSRedirects, tgDefaultIPs, tgWSPort, tgTCPPort
	defer func() { tgWSRedirects, tgDefaultIPs, tgWSPort, tgTCPPort = oldRedir, oldIPs, oldWS, oldTCP }()
	tgWSRedirects = map[int]string{2: "127.0.0.1"}
	tgDefaultIPs = map[int]string{5: "127.0.0.1"}
	tgWSPort = atoi(wsPort)
	tgTCPPort = atoi(tcpPort)

	secretHex := NewTGSecret()
	secret, _ := hex.DecodeString(secretHex)
	// порт для прокси
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := l.Addr().String()
	l.Close()
	var logs []string
	var logMu sync.Mutex
	if err := StartTG(addr, secretHex, "", false, logFunc(func(s string) {
		logMu.Lock()
		logs = append(logs, s)
		logMu.Unlock()
	})); err != nil {
		t.Fatal(err)
	}
	defer StopTG()

	run := func(dc int16, wantFrames bool) {
		c, err := net.Dial("tcp", addr)
		if err != nil {
			t.Fatal(err)
		}
		defer c.Close()
		cl := newClientSide(secret, dc, tgTagSecure)
		c.Write(cl.init)
		// три пакета одной записью и один большой — частями
		var plain []byte
		plain = append(plain, packet(16, 1)...)
		plain = append(plain, packet(100, 2)...)
		plain = append(plain, packet(8, 3)...)
		big := packet(5000, 4)
		enc := make([]byte, len(plain))
		cl.enc.XORKeyStream(enc, plain)
		c.Write(enc)
		encBig := make([]byte, len(big))
		cl.enc.XORKeyStream(encBig, big)
		c.Write(encBig[:1000])
		time.Sleep(50 * time.Millisecond)
		c.Write(encBig[1000:])
		want := append(append([]byte{}, plain...), big...)
		got := make([]byte, len(want))
		c.SetReadDeadline(time.Now().Add(5 * time.Second))
		if _, err := io.ReadFull(c, got); err != nil {
			t.Fatalf("DC%d: ответ не пришёл: %v (журнал: %v)", dc, err, logs)
		}
		cl.dec.XORKeyStream(got, got)
		if !bytes.Equal(got, want) {
			t.Fatalf("DC%d: данные не совпали", dc)
		}
	}
	run(2, true)
	mu.Lock()
	if len(frames) != 4 || frames[0] != 20 || frames[1] != 104 || frames[2] != 12 || frames[3] != 5004 {
		t.Fatalf("кадры WebSocket должны совпадать с пакетами MTProto: %v", frames)
	}
	if len(hosts) == 0 || hosts[0] != "kws2.web.telegram.org" {
		t.Fatalf("Host: %v", hosts)
	}
	mu.Unlock()
	run(5, false)
	logMu.Lock()
	joined := strings.Join(logs, "\n")
	logMu.Unlock()
	if !strings.Contains(joined, "DC2 → WebSocket") || !strings.Contains(joined, "DC5 → напрямую") {
		t.Fatalf("журнал: %s", joined)
	}
	if !strings.Contains(TGStats(), "соединений") {
		t.Fatal(TGStats())
	}
	// неверный секрет — соединение не проходит
	c, _ := net.Dial("tcp", addr)
	bad := newClientSide(make([]byte, 16), 2, tgTagSecure)
	c.Write(bad.init)
	c.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
	if n, _ := c.Read(make([]byte, 10)); n != 0 {
		t.Fatal("с неверным секретом пришёл ответ")
	}
	c.Close()
}

func TestTGDecodeDomain(t *testing.T) {
	// как _dd в tg-ws-proxy: сдвиг на число букв, .com → .co.uk
	if got := tgDecodeDomain("bcd.com"); got != "yza.co.uk" {
		t.Fatal(got)
	}
	if got := tgDecodeDomain("example.org"); got != "example.org" {
		t.Fatal(got)
	}
}

type logFunc func(string)

func (f logFunc) WriteLog(s string) { f(s) }

func atoi(s string) int {
	n := 0
	for _, c := range s {
		n = n*10 + int(c-'0')
	}
	return n
}

// Оператор не пропускает к адресу WebSocket Telegram: прокси не ждёт его на каждом соединении,
// а сразу идёт к Cloudflare-прокси, пробуя несколько одновременно.
func TestTGFallbackWhenWSUnreachable(t *testing.T) {
	cert := selfSigned(t)
	good, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{Certificates: []tls.Certificate{cert}})
	if err != nil {
		t.Fatal(err)
	}
	defer good.Close()
	go func() {
		for {
			c, err := good.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				if _, err := http.ReadRequest(bufio.NewReader(c)); err != nil {
					return
				}
				c.Write([]byte("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"))
				io.Copy(io.Discard, c)
			}(c)
		}
	}()
	dead, _ := net.Listen("tcp", "127.0.0.1:0")
	deadAddr := dead.Addr().String()
	dead.Close()
	_, deadPort, _ := net.SplitHostPort(deadAddr)

	oldRedir, oldWS, oldCF, oldInsecure := tgWSRedirects, tgWSPort, tgCFAddr, tgInsecureTLS
	defer func() { tgWSRedirects, tgWSPort, tgCFAddr, tgInsecureTLS = oldRedir, oldWS, oldCF, oldInsecure }()
	tgWSRedirects = map[int]string{2: "127.0.0.1"}
	tgWSPort = atoi(deadPort)
	tgInsecureTLS = true
	tgCFAddr = func(domain string) string {
		if strings.HasSuffix(domain, ".good") {
			return good.Addr().String()
		}
		return deadAddr
	}
	p := &tgProxy{cf: []string{"a.bad", "b.good", "c.bad"}, useCF: true, cfByDC: map[int]string{},
		conns: map[net.Conn]struct{}{}}
	hs := &tgHandshake{dc: 2}
	if ws := p.connectWS(hs); ws != nil {
		t.Fatal("WebSocket к недоступному адресу")
	}
	if !p.wsDownUntil.After(time.Now()) {
		t.Fatal("недоступный адрес не запомнен")
	}
	start := time.Now()
	if ws := p.connectWS(hs); ws != nil || time.Since(start) > 100*time.Millisecond {
		t.Fatal("недоступный адрес пробуется снова")
	}
	ws := p.connectCF(2)
	if ws == nil {
		t.Fatal("Cloudflare-прокси не найден")
	}
	ws.conn.Close()
	if p.cfByDC[2] != "b.good" {
		t.Fatalf("запомнен %q", p.cfByDC[2])
	}
}
