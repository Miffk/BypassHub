package bhcore

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/device"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun/netstack"
	"golang.org/x/crypto/curve25519"
)

func keypair(t *testing.T) (priv, pub []byte) {
	priv = make([]byte, 32)
	_, _ = rand.Read(priv)
	priv[0] &= 248
	priv[31] = (priv[31] & 127) | 64
	pub, err := curve25519.X25519(priv, curve25519.Basepoint)
	if err != nil {
		t.Fatal(err)
	}
	return
}

const obfuscation = "jc=4\njmin=10\njmax=50\ns1=12\ns2=12\ns3=12\ns4=12\nh1=1\nh2=2\nh3=3\nh4=4\nrandom_trailers=true\ndisable_cookies=true\n"

// Сервер AmneziaWG в памяти: HTTP на 10.9.0.1:80 и UDP-эхо на 10.9.0.1:7.
func startServer(t *testing.T, serverPriv, clientPub []byte) (int, func()) {
	tunDev, tnet, err := netstack.CreateNetTUN([]netip.Addr{netip.MustParseAddr("10.9.0.1")},
		[]netip.Addr{netip.MustParseAddr("1.1.1.1")}, 1280)
	if err != nil {
		t.Fatal(err)
	}
	dev := device.NewDevice(tunDev, conn.NewDefaultBind(), device.NewLogger(device.LogLevelError, "server: "))
	pc, _ := net.ListenPacket("udp", "127.0.0.1:0")
	port := pc.LocalAddr().(*net.UDPAddr).Port
	pc.Close()
	uapi := fmt.Sprintf("private_key=%s\nlisten_port=%d\n%spublic_key=%s\nallowed_ip=10.9.0.2/32\n",
		hex.EncodeToString(serverPriv), port, obfuscation, hex.EncodeToString(clientPub))
	if err := dev.IpcSet(uapi); err != nil {
		t.Fatal(err)
	}
	if err := dev.Up(); err != nil {
		t.Fatal(err)
	}
	ln, err := tnet.ListenTCP(&net.TCPAddr{Port: 80})
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "hello from awg")
	}))
	udp, err := tnet.ListenUDP(&net.UDPAddr{Port: 7})
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		buf := make([]byte, 2048)
		for {
			n, from, err := udp.ReadFrom(buf)
			if err != nil {
				return
			}
			_, _ = udp.WriteTo(append([]byte("echo:"), buf[:n]...), from)
		}
	}()
	return port, func() { ln.Close(); udp.Close(); dev.Close() }
}

func socksConnect(t *testing.T, port int32, host string, dport uint16) net.Conn {
	c, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = c.Write([]byte{5, 1, 0})
	resp := make([]byte, 2)
	_, _ = io.ReadFull(c, resp)
	req := []byte{5, 1, 0, 3, byte(len(host))}
	req = append(req, host...)
	req = binary.BigEndian.AppendUint16(req, dport)
	_, _ = c.Write(req)
	rep := make([]byte, 10)
	if _, err := io.ReadFull(c, rep); err != nil || rep[1] != 0 {
		t.Fatalf("socks connect: %v %v", rep, err)
	}
	return c
}

func TestAWGThroughSocks(t *testing.T) {
	serverPriv, serverPub := keypair(t)
	clientPriv, clientPub := keypair(t)
	port, stop := startServer(t, serverPriv, clientPub)
	defer stop()

	b64 := base64.StdEncoding.EncodeToString
	conf := fmt.Sprintf(`[Interface]
MTU = 1280
Address = 10.9.0.2/32
DNS = 1.1.1.1
PrivateKey = %s
Jc = 4
Jmin = 10
Jmax = 50
S1 = 12
S2 = 12
S3 = 12
S4 = 12
H1 = 1
H2 = 2
H3 = 3
H4 = 4
I1 = <r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>
RekeyAfterTime = 100-120
RejectAfterTime = 150-180
KeepaliveTimeout = 5-15
RandomTrailers = on
DisableCookies = on
Table = off

[Peer]
PublicKey = %s
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 127.0.0.1:%d
PersistentKeepalive = 25-35
`, b64(clientPriv), b64(serverPub), port)

	logPlatform = testLog{t}
	socksPort, err := StartAWG(conf)
	if err != nil {
		t.Fatal(err)
	}
	defer StopAWG()

	// TCP: HTTP-запрос через SOCKS5 → туннель → сервер
	c := socksConnect(t, socksPort, "10.9.0.1", 80)
	_ = c.SetDeadline(time.Now().Add(10 * time.Second))
	_, _ = io.WriteString(c, "GET / HTTP/1.0\r\nHost: x\r\n\r\n")
	body, _ := io.ReadAll(c)
	c.Close()
	if !strings.Contains(string(body), "hello from awg") {
		t.Fatalf("http через туннель: %q", body)
	}

	// UDP ASSOCIATE: датаграмма → эхо
	ctrl, _ := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", socksPort))
	defer ctrl.Close()
	_, _ = ctrl.Write([]byte{5, 1, 0})
	_, _ = io.ReadFull(ctrl, make([]byte, 2))
	_, _ = ctrl.Write([]byte{5, 3, 0, 1, 0, 0, 0, 0, 0, 0})
	rep := make([]byte, 10)
	if _, err := io.ReadFull(ctrl, rep); err != nil || rep[1] != 0 {
		t.Fatalf("udp associate: %v %v", rep, err)
	}
	relay := &net.UDPAddr{IP: net.IP(rep[4:8]), Port: int(binary.BigEndian.Uint16(rep[8:10]))}
	u, _ := net.DialUDP("udp", nil, relay)
	defer u.Close()
	pkt := []byte{0, 0, 0, 1, 10, 9, 0, 1, 0, 7}
	pkt = append(pkt, "ping"...)
	_, _ = u.Write(pkt)
	_ = u.SetReadDeadline(time.Now().Add(10 * time.Second))
	buf := make([]byte, 2048)
	n, err := u.Read(buf)
	if err != nil {
		t.Fatalf("udp echo: %v", err)
	}
	if got := string(buf[10:n]); got != "echo:ping" {
		t.Fatalf("udp echo: %q", got)
	}
}

func TestParseConfToUAPI(t *testing.T) {
	_, pub := keypair(t)
	priv, _ := keypair(t)
	b64 := base64.StdEncoding.EncodeToString
	cfg, err := parseAWGConf(fmt.Sprintf("[Interface]\nAddress = 10.8.1.2/32\nPrivateKey = %s\nHeaderProtectionKey = %s\n"+
		"RandomTrailers = on\n[Peer]\nPublicKey = %s\nEndpoint = 203.0.113.5:51820\nAllowedIPs = 0.0.0.0/1, 128.0.0.0/1\n",
		b64(priv), b64(pub), b64(pub)))
	if err != nil {
		t.Fatal(err)
	}
	u, err := cfg.toUAPI()
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"private_key=" + hex.EncodeToString(priv), "header_protection_key=",
		"random_trailers=true", "endpoint=203.0.113.5:51820", "allowed_ip=128.0.0.0/1",
		"persistent_keepalive_interval=0"} {
		if !strings.Contains(u, want) {
			t.Fatalf("нет %q в\n%s", want, u)
		}
	}
	if EndpointHost("[Interface]\nAddress=10.0.0.2/32\nPrivateKey="+b64(priv)+"\n[Peer]\nEndpoint=198.51.100.7:1\n") != "198.51.100.7" {
		t.Fatal("EndpointHost")
	}
}

type testLog struct{ t *testing.T }

func (l testLog) OpenTun(string) (int32, error) { return 0, nil }
func (l testLog) Protect(int32) bool            { return true }
func (l testLog) InterfacesJSON() string        { return "[]" }
func (l testLog) WriteLog(m string)             { l.t.Log(m) }

func TestSingBoxOverAWG(t *testing.T) {
	serverPriv, serverPub := keypair(t)
	clientPriv, clientPub := keypair(t)
	port, stop := startServer(t, serverPriv, clientPub)
	defer stop()
	b64 := base64.StdEncoding.EncodeToString
	conf := fmt.Sprintf("[Interface]\nAddress = 10.9.0.2/32\nPrivateKey = %s\nJc = 4\nJmin = 10\nJmax = 50\n"+
		"S1 = 12\nS2 = 12\nS3 = 12\nS4 = 12\nH1 = 1\nH2 = 2\nH3 = 3\nH4 = 4\nRandomTrailers = on\nDisableCookies = on\n"+
		"[Peer]\nPublicKey = %s\nAllowedIPs = 0.0.0.0/0\nEndpoint = 127.0.0.1:%d\n", b64(clientPriv), b64(serverPub), port)
	socksPort, err := StartAWG(conf)
	if err != nil {
		t.Fatal(err)
	}
	defer StopAWG()
	dir := t.TempDir()
	if err := Setup(dir+"/work", dir+"/tmp"); err != nil {
		t.Fatal(err)
	}
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	mixed := ln.Addr().(*net.TCPAddr).Port
	ln.Close()
	config := fmt.Sprintf(`{
	  "log": {"level": "warn"},
	  "inbounds": [{"type": "mixed", "tag": "in", "listen": "127.0.0.1", "listen_port": %d}],
	  "outbounds": [{"type": "socks", "tag": "proxy", "server": "127.0.0.1", "server_port": %d, "version": "5"},
	                {"type": "direct", "tag": "direct"}],
	  "route": {"final": "proxy"}
	}`, mixed, socksPort)
	if err := CheckConfig(config); err != nil {
		t.Fatal(err)
	}
	if err := Start(config, testLog{t}); err != nil {
		t.Fatal(err)
	}
	defer Stop()
	if !IsRunning() {
		t.Fatal("not running")
	}
	client := &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{
		Proxy: func(*http.Request) (*url.URL, error) { return url.Parse(fmt.Sprintf("http://127.0.0.1:%d", mixed)) },
	}}
	resp, err := client.Get("http://10.9.0.1/")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != "hello from awg" {
		t.Fatalf("got %q", body)
	}
}
