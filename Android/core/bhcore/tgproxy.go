package bhcore

// TG WS Proxy — порт tg-ws-proxy (Flowseal, MIT) для Android.
//
// Telegram подключается к локальному MTProto-прокси (секрет dd…). Прокси расшифровывает
// обфусцированное рукопожатие, узнаёт дата-центр и отправляет трафик в Telegram через
// WebSocket — так же, как ходит web.telegram.org (kws<N>.web.telegram.org/apiws): такие
// соединения провайдеры не замедляют. Если WebSocket недоступен — запасные пути: прокси-домены
// на Cloudflare (как в tg-ws-proxy) и прямое TCP-подключение к дата-центру.

import (
	"bufio"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	mrand "math/rand"
	"net"
	"strings"
	"sync"
	"time"
)

const (
	tgHandshakeLen = 64
	tgSkipLen      = 8
	tgPrekeyLen    = 32
	tgIVLen        = 16
	tgProtoTagPos  = 56
	tgDCIdxPos     = 60
)

var (
	tgTagAbridged     = []byte{0xef, 0xef, 0xef, 0xef}
	tgTagIntermediate = []byte{0xee, 0xee, 0xee, 0xee}
	tgTagSecure       = []byte{0xdd, 0xdd, 0xdd, 0xdd}

	tgReservedStarts = [][]byte{
		[]byte("HEAD"), []byte("POST"), []byte("GET "), {0xee, 0xee, 0xee, 0xee},
		{0xdd, 0xdd, 0xdd, 0xdd}, {0x16, 0x03, 0x01, 0x02}, []byte("OPTI"),
	}

	// Дата-центры, доступные через WebSocket web.telegram.org (как в tg-ws-proxy по умолчанию).
	tgWSRedirects = map[int]string{2: "149.154.167.220", 4: "149.154.167.220"}
	tgDefaultIPs  = map[int]string{1: "149.154.175.50", 2: "149.154.167.51", 3: "149.154.175.100",
		4: "149.154.167.91", 5: "149.154.171.5", 203: "91.105.192.100"}
	tgTestIPs = map[int]string{1: "149.154.175.10", 2: "149.154.167.40", 3: "149.154.175.117"}

	// Можно подменить в тестах.
	tgWSPort      = 443
	tgTCPPort     = 443
	tgInsecureTLS = false
	tgFrontingSNI = "sprinthost.ru"
)

// Зашифрованный список доменов Cloudflare-прокси из tg-ws-proxy (расшифровка — tgDecodeDomain).
var tgCFEncoded = []string{
	"virkgj.com", "vmmzovy.com", "mkuosckvso.com", "zaewayzmplad.com", "twdmbzcm.com", "awzwsldi.com",
	"clngqrflngqin.com", "tjacxbqtj.com", "bxaxtxmrw.com", "dmohrsgmohcrwb.com", "vwbmtmoi.com", "khgrre.com",
	"ulihssf.com", "tmhqsdqmfpmk.com", "xwuwoqbm.com", "orgcnunpj.com", "zhkuldz.com", "zypoljnslxa.com",
	"efabnxaowuzs.com", "zaftuzsftqdq.com",
}

// tgDecodeDomain — как _dd в tg-ws-proxy: сдвиг букв на их количество, «.com» → «.co.uk».
func tgDecodeDomain(s string) string {
	s = strings.TrimSpace(s)
	if !strings.HasSuffix(s, ".com") {
		return s
	}
	p := s[:len(s)-4]
	n := 0
	for _, c := range p {
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') {
			n++
		}
	}
	var b strings.Builder
	for _, c := range p {
		switch {
		case c >= 'a' && c <= 'z':
			b.WriteRune('a' + ((c-'a'-rune(n))%26+26)%26)
		case c >= 'A' && c <= 'Z':
			b.WriteRune('A' + ((c-'A'-rune(n))%26+26)%26)
		default:
			b.WriteRune(c)
		}
	}
	return b.String() + ".co.uk"
}

// ---------------------------------------------------------------- шифрование

func tgCTR(key, iv []byte) cipher.Stream {
	block, err := aes.NewCipher(key)
	if err != nil {
		panic(err)
	}
	return cipher.NewCTR(block, iv)
}

func tgSkip(s cipher.Stream, n int) {
	b := make([]byte, n)
	s.XORKeyStream(b, b)
}

func tgReverse(b []byte) []byte {
	out := make([]byte, len(b))
	for i := range b {
		out[i] = b[len(b)-1-i]
	}
	return out
}

func tgKey(prekey, secret []byte) []byte {
	h := sha256.Sum256(append(append([]byte{}, prekey...), secret...))
	return h[:]
}

type tgHandshake struct {
	dc     int
	media  bool
	test   bool
	tag    []byte
	preIV  []byte // 48 байт из рукопожатия клиента
	dcIdx  int16
	secure bool
}

func tgParseHandshake(h, secret []byte) (*tgHandshake, bool) {
	preIV := h[tgSkipLen : tgSkipLen+tgPrekeyLen+tgIVLen]
	dec := make([]byte, tgHandshakeLen)
	tgCTR(tgKey(preIV[:tgPrekeyLen], secret), preIV[tgPrekeyLen:]).XORKeyStream(dec, h)
	tag := dec[tgProtoTagPos : tgProtoTagPos+4]
	if !bytesEq(tag, tgTagAbridged) && !bytesEq(tag, tgTagIntermediate) && !bytesEq(tag, tgTagSecure) {
		return nil, false
	}
	idx := int16(binary.LittleEndian.Uint16(dec[tgDCIdxPos:]))
	dc := int(idx)
	if dc < 0 {
		dc = -dc
	}
	hs := &tgHandshake{dc: dc, media: idx < 0, tag: append([]byte{}, tag...), preIV: append([]byte{}, preIV...)}
	if hs.dc >= 10000 {
		hs.test = true
		hs.dc -= 10000
	}
	hs.dcIdx = int16(hs.dc)
	if hs.media {
		hs.dcIdx = -hs.dcIdx
	}
	return hs, true
}

func bytesEq(a, b []byte) bool { return string(a) == string(b) }

// tgRelayInit — рукопожатие к Telegram (обычная обфускация, без секрета).
func tgRelayInit(tag []byte, dcIdx int16) []byte {
	rnd := make([]byte, tgHandshakeLen)
	for {
		_, _ = rand.Read(rnd)
		if rnd[0] == 0xef {
			continue
		}
		bad := false
		for _, r := range tgReservedStarts {
			if bytesEq(rnd[:4], r) {
				bad = true
			}
		}
		if bad || bytesEq(rnd[4:8], []byte{0, 0, 0, 0}) {
			continue
		}
		break
	}
	enc := make([]byte, tgHandshakeLen)
	tgCTR(rnd[tgSkipLen:tgSkipLen+tgPrekeyLen], rnd[tgSkipLen+tgPrekeyLen:tgSkipLen+tgPrekeyLen+tgIVLen]).
		XORKeyStream(enc, rnd)
	tail := make([]byte, 8)
	copy(tail, tag)
	binary.LittleEndian.PutUint16(tail[4:], uint16(dcIdx))
	_, _ = rand.Read(tail[6:])
	out := append([]byte{}, rnd...)
	for i := 0; i < 8; i++ {
		out[tgProtoTagPos+i] = tail[i] ^ (enc[tgProtoTagPos+i] ^ rnd[tgProtoTagPos+i])
	}
	return out
}

// tgCrypto: clt* — сторона клиента (Telegram на телефоне), tg* — сторона сервера Telegram.
type tgCrypto struct {
	cltDec, cltEnc, tgEnc, tgDec cipher.Stream
}

func tgNewCrypto(preIV, secret, relay []byte) *tgCrypto {
	c := &tgCrypto{}
	c.cltDec = tgCTR(tgKey(preIV[:tgPrekeyLen], secret), preIV[tgPrekeyLen:])
	tgSkip(c.cltDec, tgHandshakeLen)
	rev := tgReverse(preIV)
	c.cltEnc = tgCTR(tgKey(rev[:tgPrekeyLen], secret), rev[tgPrekeyLen:])
	c.tgEnc = tgCTR(relay[tgSkipLen:tgSkipLen+tgPrekeyLen], relay[tgSkipLen+tgPrekeyLen:tgSkipLen+tgPrekeyLen+tgIVLen])
	tgSkip(c.tgEnc, tgHandshakeLen)
	rrev := tgReverse(relay[tgSkipLen : tgSkipLen+tgPrekeyLen+tgIVLen])
	c.tgDec = tgCTR(rrev[:tgPrekeyLen], rrev[tgPrekeyLen:])
	return c
}

// ---------------------------------------------------------------- деление на пакеты MTProto

// tgSplitter делит поток на отдельные транспортные пакеты MTProto: каждый уходит своим кадром WebSocket.
type tgSplitter struct {
	abridged bool
	cipher   []byte
	plain    []byte
	disabled bool
}

func (s *tgSplitter) split(chunk, plain []byte) [][]byte {
	if s.disabled {
		return [][]byte{chunk}
	}
	s.cipher = append(s.cipher, chunk...)
	s.plain = append(s.plain, plain...)
	var parts [][]byte
	off := 0
	for off < len(s.cipher) {
		n, ok := s.next(off)
		if !ok {
			break
		}
		if n <= 0 {
			parts = append(parts, append([]byte{}, s.cipher[off:]...))
			off = len(s.cipher)
			s.disabled = true
			break
		}
		parts = append(parts, append([]byte{}, s.cipher[off:off+n]...))
		off += n
	}
	s.cipher = append(s.cipher[:0], s.cipher[off:]...)
	s.plain = append(s.plain[:0], s.plain[off:]...)
	return parts
}

func (s *tgSplitter) flush() []byte {
	if len(s.cipher) == 0 {
		return nil
	}
	out := append([]byte{}, s.cipher...)
	s.cipher, s.plain = s.cipher[:0], s.plain[:0]
	return out
}

// next — длина пакета, начинающегося с off; ok=false — пакет ещё не пришёл целиком.
func (s *tgSplitter) next(off int) (int, bool) {
	avail := len(s.plain) - off
	if avail <= 0 {
		return 0, false
	}
	if s.abridged {
		first := s.plain[off]
		var payload, header int
		if first == 0x7f || first == 0xff {
			if avail < 4 {
				return 0, false
			}
			payload = int(uint32(s.plain[off+1])|uint32(s.plain[off+2])<<8|uint32(s.plain[off+3])<<16) * 4
			header = 4
		} else {
			payload = int(first&0x7f) * 4
			header = 1
		}
		if payload <= 0 {
			return 0, true
		}
		if avail < header+payload {
			return 0, false
		}
		return header + payload, true
	}
	if avail < 4 {
		return 0, false
	}
	payload := int(binary.LittleEndian.Uint32(s.plain[off:]) & 0x7fffffff)
	if payload <= 0 {
		return 0, true
	}
	if avail < 4+payload {
		return 0, false
	}
	return 4 + payload, true
}

// ---------------------------------------------------------------- WebSocket

type tgWS struct {
	conn net.Conn
	br   *bufio.Reader
	wmu  sync.Mutex
	frag []byte
}

func tgDialWS(addr, host, sni string, verify bool, path string, timeout time.Duration) (*tgWS, error) {
	d := net.Dialer{Timeout: timeout}
	raw, err := d.Dial("tcp", addr)
	if err != nil {
		return nil, err
	}
	conn := tls.Client(raw, &tls.Config{ServerName: sni, InsecureSkipVerify: !verify || tgInsecureTLS,
		NextProtos: []string{"http/1.1"}})
	_ = conn.SetDeadline(time.Now().Add(timeout))
	if err := conn.Handshake(); err != nil {
		raw.Close()
		return nil, err
	}
	key := make([]byte, 16)
	_, _ = rand.Read(key)
	req := "GET " + path + " HTTP/1.1\r\nHost: " + host + "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
		"Sec-WebSocket-Key: " + base64.StdEncoding.EncodeToString(key) + "\r\nSec-WebSocket-Version: 13\r\n" +
		"Sec-WebSocket-Protocol: binary\r\n\r\n"
	if _, err := conn.Write([]byte(req)); err != nil {
		conn.Close()
		return nil, err
	}
	br := bufio.NewReaderSize(conn, 64*1024)
	status, err := br.ReadString('\n')
	if err != nil {
		conn.Close()
		return nil, err
	}
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			conn.Close()
			return nil, err
		}
		if line == "\r\n" || line == "\n" {
			break
		}
	}
	if f := strings.Fields(status); len(f) < 2 || f[1] != "101" {
		conn.Close()
		return nil, fmt.Errorf("WebSocket: %s", strings.TrimSpace(status))
	}
	_ = conn.SetDeadline(time.Time{})
	return &tgWS{conn: conn, br: br}, nil
}

func tgFrame(op byte, data []byte) []byte {
	n := len(data)
	h := []byte{0x80 | op}
	switch {
	case n < 126:
		h = append(h, 0x80|byte(n))
	case n < 65536:
		h = append(h, 0x80|126, byte(n>>8), byte(n))
	default:
		h = append(h, 0x80|127)
		h = binary.BigEndian.AppendUint64(h, uint64(n))
	}
	mask := make([]byte, 4)
	_, _ = rand.Read(mask)
	h = append(h, mask...)
	out := make([]byte, len(h)+n)
	copy(out, h)
	for i, b := range data {
		out[len(h)+i] = b ^ mask[i&3]
	}
	return out
}

func (w *tgWS) send(parts ...[]byte) error {
	var buf []byte
	for _, p := range parts {
		buf = append(buf, tgFrame(0x2, p)...)
	}
	w.wmu.Lock()
	defer w.wmu.Unlock()
	_, err := w.conn.Write(buf)
	return err
}

// recv — следующее сообщение; io.EOF — сервер закрыл соединение.
func (w *tgWS) recv() ([]byte, error) {
	for {
		var hdr [2]byte
		if _, err := io.ReadFull(w.br, hdr[:]); err != nil {
			return nil, err
		}
		fin := hdr[0]&0x80 != 0
		op := hdr[0] & 0x0f
		n := uint64(hdr[1] & 0x7f)
		if n == 126 {
			var b [2]byte
			if _, err := io.ReadFull(w.br, b[:]); err != nil {
				return nil, err
			}
			n = uint64(binary.BigEndian.Uint16(b[:]))
		} else if n == 127 {
			var b [8]byte
			if _, err := io.ReadFull(w.br, b[:]); err != nil {
				return nil, err
			}
			n = binary.BigEndian.Uint64(b[:])
		}
		if n > 16<<20 {
			return nil, errors.New("слишком большой кадр WebSocket")
		}
		var mask []byte
		if hdr[1]&0x80 != 0 {
			mask = make([]byte, 4)
			if _, err := io.ReadFull(w.br, mask); err != nil {
				return nil, err
			}
		}
		payload := make([]byte, n)
		if _, err := io.ReadFull(w.br, payload); err != nil {
			return nil, err
		}
		if mask != nil {
			for i := range payload {
				payload[i] ^= mask[i&3]
			}
		}
		switch op {
		case 0x8:
			w.wmu.Lock()
			_, _ = w.conn.Write(tgFrame(0x8, payload[:min(len(payload), 2)]))
			w.wmu.Unlock()
			return nil, io.EOF
		case 0x9:
			w.wmu.Lock()
			_, _ = w.conn.Write(tgFrame(0xA, payload))
			w.wmu.Unlock()
			continue
		case 0xA:
			continue
		case 0x0, 0x1, 0x2:
			if fin && len(w.frag) == 0 {
				return payload, nil
			}
			w.frag = append(w.frag, payload...)
			if !fin {
				continue
			}
			msg := w.frag
			w.frag = nil
			return msg, nil
		}
	}
}

func (w *tgWS) close() {
	w.wmu.Lock()
	_, _ = w.conn.Write(tgFrame(0x8, nil))
	w.wmu.Unlock()
	_ = w.conn.Close()
}

// ---------------------------------------------------------------- прокси

type tgProxy struct {
	ln       net.Listener
	secret   []byte
	cf       []string
	useCF    bool
	fronted  bool // последний удачный способ подключения WebSocket — через подставное имя
	mu       sync.Mutex
	cfByDC   map[int]string
	conns    map[net.Conn]struct{}
	closed   bool
	upBytes  int64
	dnBytes  int64
	sessions int64
}

var (
	tgMu      sync.Mutex
	tgCurrent *tgProxy
	tgLogger  Logger
)

// Logger — журнал для TG-прокси (Kotlin).
type Logger interface {
	WriteLog(message string)
}

func tgLogf(format string, args ...any) {
	msg := "TG: " + fmt.Sprintf(format, args...)
	tgMu.Lock()
	l := tgLogger
	tgMu.Unlock()
	if l != nil {
		l.WriteLog(msg)
	}
}

// StartTG запускает прокси на listen (например 127.0.0.1:1443). secretHex — 32 hex-символа;
// cfDomains — список доменов Cloudflare-прокси (по строке, в зашифрованном виде из tg-ws-proxy;
// пусто — встроенный список); useCF — использовать их как запасной путь.
func StartTG(listen, secretHex, cfDomains string, useCF bool, logger Logger) error {
	secret, err := hex.DecodeString(strings.TrimSpace(secretHex))
	if err != nil || len(secret) != 16 {
		return errors.New("секрет — 32 шестнадцатеричных символа")
	}
	StopTG()
	ln, err := net.Listen("tcp", listen)
	if err != nil {
		return err
	}
	var cf []string
	seen := map[string]bool{}
	src := tgCFEncoded
	if lines := strings.Fields(cfDomains); len(lines) >= 3 {
		src = lines
	}
	for _, d := range src {
		if strings.HasPrefix(d, "#") {
			continue
		}
		dd := strings.ToLower(tgDecodeDomain(d))
		if dd != "" && strings.Contains(dd, ".") && !seen[dd] {
			seen[dd] = true
			cf = append(cf, dd)
		}
	}
	p := &tgProxy{ln: ln, secret: secret, cf: cf, useCF: useCF, cfByDC: map[int]string{}, conns: map[net.Conn]struct{}{}}
	tgMu.Lock()
	tgCurrent = p
	tgLogger = logger
	tgMu.Unlock()
	tgLogf("прокси запущен на %s (Cloudflare-резерв: %v, доменов %d)", ln.Addr(), useCF, len(cf))
	go p.serve()
	return nil
}

// StopTG останавливает прокси и закрывает все соединения.
func StopTG() {
	tgMu.Lock()
	p := tgCurrent
	tgCurrent = nil
	tgMu.Unlock()
	if p == nil {
		return
	}
	p.mu.Lock()
	p.closed = true
	for c := range p.conns {
		_ = c.Close()
	}
	p.mu.Unlock()
	_ = p.ln.Close()
	tgLogf("прокси остановлен")
}

// TGRunning — работает ли прокси.
func TGRunning() bool {
	tgMu.Lock()
	defer tgMu.Unlock()
	return tgCurrent != nil
}

// TGStats — «соединений N · ↑X ↓Y» для экрана.
func TGStats() string {
	tgMu.Lock()
	p := tgCurrent
	tgMu.Unlock()
	if p == nil {
		return ""
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return fmt.Sprintf("соединений: %d · ↑ %s ↓ %s", len(p.conns), humanBytes(p.upBytes), humanBytes(p.dnBytes))
}

func humanBytes(n int64) string {
	f := float64(n)
	for _, u := range []string{"Б", "КБ", "МБ", "ГБ"} {
		if f < 1024 {
			return fmt.Sprintf("%.1f %s", f, u)
		}
		f /= 1024
	}
	return fmt.Sprintf("%.1f ТБ", f)
}

func (p *tgProxy) track(c net.Conn, add bool) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if add {
		if p.closed {
			return false
		}
		p.conns[c] = struct{}{}
	} else {
		delete(p.conns, c)
	}
	return true
}

func (p *tgProxy) count(up, down int) {
	p.mu.Lock()
	p.upBytes += int64(up)
	p.dnBytes += int64(down)
	p.mu.Unlock()
}

func (p *tgProxy) serve() {
	for {
		c, err := p.ln.Accept()
		if err != nil {
			return
		}
		go p.handle(c)
	}
}

func (p *tgProxy) handle(c net.Conn) {
	if !p.track(c, true) {
		c.Close()
		return
	}
	defer func() {
		p.track(c, false)
		c.Close()
	}()
	_ = c.SetReadDeadline(time.Now().Add(10 * time.Second))
	init := make([]byte, tgHandshakeLen)
	if _, err := io.ReadFull(c, init); err != nil {
		return
	}
	_ = c.SetReadDeadline(time.Time{})
	hs, ok := tgParseHandshake(init, p.secret)
	if !ok {
		tgLogf("неверное рукопожатие — проверьте секрет прокси в Telegram")
		_, _ = io.Copy(io.Discard, c)
		return
	}
	relay := tgRelayInit(hs.tag, hs.dcIdx)
	cr := tgNewCrypto(hs.preIV, p.secret, relay)
	media := ""
	if hs.media {
		media = " медиа"
	}
	splitter := &tgSplitter{abridged: bytesEq(hs.tag, tgTagAbridged)}

	if ws := p.connectWS(hs); ws != nil {
		tgLogf("DC%d%s → WebSocket", hs.dc, media)
		p.bridgeWS(c, ws, relay, cr, splitter)
		return
	}
	if p.useCF && !hs.test {
		if ws := p.connectCF(hs.dc); ws != nil {
			tgLogf("DC%d%s → Cloudflare", hs.dc, media)
			p.bridgeWS(c, ws, relay, cr, splitter)
			return
		}
	}
	table := tgDefaultIPs
	if hs.test {
		table = tgTestIPs
	}
	if ip, ok := table[hs.dc]; ok {
		up, err := net.DialTimeout("tcp", net.JoinHostPort(ip, fmt.Sprint(tgTCPPort)), 10*time.Second)
		if err == nil {
			tgLogf("DC%d%s → напрямую (TCP)", hs.dc, media)
			p.bridgeTCP(c, up, relay, cr)
			return
		}
		tgLogf("DC%d%s: прямое подключение не удалось: %v", hs.dc, media, err)
	}
	tgLogf("DC%d%s: нет доступного пути к Telegram", hs.dc, media)
}

func tgWSDomains(dc int, media bool) []string {
	if dc == 203 {
		dc = 2
	}
	if !media {
		return []string{fmt.Sprintf("kws%d.web.telegram.org", dc)}
	}
	return []string{fmt.Sprintf("kws%d-1.web.telegram.org", dc), fmt.Sprintf("kws%d.web.telegram.org", dc)}
}

func (p *tgProxy) connectWS(hs *tgHandshake) *tgWS {
	ip, ok := tgWSRedirects[hs.dc]
	if !ok {
		return nil
	}
	path := "/apiws"
	if hs.test {
		path = "/apiws_test"
	}
	addr := net.JoinHostPort(ip, fmt.Sprint(tgWSPort))
	for _, domain := range tgWSDomains(hs.dc, hs.media) {
		p.mu.Lock()
		order := []bool{false, true}
		if p.fronted {
			order = []bool{true, false}
		}
		p.mu.Unlock()
		for _, fronted := range order {
			sni := domain
			if fronted {
				sni = tgFrontingSNI
			}
			ws, err := tgDialWS(addr, domain, sni, !fronted, path, 8*time.Second)
			if err != nil {
				tgLogf("WebSocket %s (подставное имя: %v): %v", domain, fronted, err)
				continue
			}
			p.mu.Lock()
			p.fronted = fronted
			p.mu.Unlock()
			return ws
		}
	}
	return nil
}

func (p *tgProxy) connectCF(dc int) *tgWS {
	p.mu.Lock()
	order := []string{}
	if cur, ok := p.cfByDC[dc]; ok {
		order = append(order, cur)
	}
	shuffled := append([]string{}, p.cf...)
	mrand.Shuffle(len(shuffled), func(i, j int) { shuffled[i], shuffled[j] = shuffled[j], shuffled[i] })
	for _, d := range shuffled {
		if len(order) == 0 || d != order[0] {
			order = append(order, d)
		}
	}
	p.mu.Unlock()
	for i, base := range order {
		if i >= 6 { // не перебираем бесконечно: остальные — при следующем соединении
			break
		}
		domain := fmt.Sprintf("kws%d.%s", dc, base)
		ws, err := tgDialWS(net.JoinHostPort(domain, "443"), domain, domain, true, "/apiws", 10*time.Second)
		if err != nil {
			tgLogf("Cloudflare-прокси %s: %v", domain, err)
			continue
		}
		p.mu.Lock()
		p.cfByDC[dc] = base
		p.mu.Unlock()
		return ws
	}
	return nil
}

func (p *tgProxy) bridgeWS(c net.Conn, ws *tgWS, relay []byte, cr *tgCrypto, splitter *tgSplitter) {
	if !p.track(ws.conn, true) {
		ws.close()
		return
	}
	defer func() {
		p.track(ws.conn, false)
		ws.close()
	}()
	if err := ws.send(relay); err != nil {
		return
	}
	done := make(chan struct{}, 2)
	go func() { // телефон → Telegram
		defer func() { done <- struct{}{} }()
		buf := make([]byte, 64*1024)
		for {
			n, err := c.Read(buf)
			if n > 0 {
				plain := make([]byte, n)
				cr.cltDec.XORKeyStream(plain, buf[:n])
				enc := make([]byte, n)
				cr.tgEnc.XORKeyStream(enc, plain)
				p.count(n, 0)
				var parts [][]byte
				if splitter != nil {
					parts = splitter.split(enc, plain)
				} else {
					parts = [][]byte{enc}
				}
				if len(parts) > 0 {
					if ws.send(parts...) != nil {
						return
					}
				}
			}
			if err != nil {
				if splitter != nil {
					if tail := splitter.flush(); tail != nil {
						_ = ws.send(tail)
					}
				}
				return
			}
		}
	}()
	go func() { // Telegram → телефон
		defer func() { done <- struct{}{} }()
		for {
			data, err := ws.recv()
			if err != nil {
				return
			}
			cr.tgDec.XORKeyStream(data, data)
			cr.cltEnc.XORKeyStream(data, data)
			p.count(0, len(data))
			if _, err := c.Write(data); err != nil {
				return
			}
		}
	}()
	<-done
	_ = c.Close()
	_ = ws.conn.Close()
	<-done
}

func (p *tgProxy) bridgeTCP(c, up net.Conn, relay []byte, cr *tgCrypto) {
	if !p.track(up, true) {
		up.Close()
		return
	}
	defer func() {
		p.track(up, false)
		up.Close()
	}()
	if _, err := up.Write(relay); err != nil {
		return
	}
	done := make(chan struct{}, 2)
	pipe := func(src, dst net.Conn, dec, enc cipher.Stream, upDir bool) {
		defer func() { done <- struct{}{} }()
		buf := make([]byte, 64*1024)
		for {
			n, err := src.Read(buf)
			if n > 0 {
				dec.XORKeyStream(buf[:n], buf[:n])
				enc.XORKeyStream(buf[:n], buf[:n])
				if upDir {
					p.count(n, 0)
				} else {
					p.count(0, n)
				}
				if _, werr := dst.Write(buf[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}
	go pipe(c, up, cr.cltDec, cr.tgEnc, true)
	go pipe(up, c, cr.tgDec, cr.cltEnc, false)
	<-done
	_ = c.Close()
	_ = up.Close()
	<-done
}

// NewTGSecret — случайный секрет (32 hex-символа).
func NewTGSecret() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
