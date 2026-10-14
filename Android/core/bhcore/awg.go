package bhcore

// AmneziaWG внутри приложения: туннель работает в пространстве пользователя
// (сетевой стек gVisor), а наружу отдаётся локальный SOCKS5 на 127.0.0.1.
// sing-box отправляет в него «зарубежный» трафик — так раздельное туннелирование
// работает и для AmneziaWG, а на телефоне остаётся один VPN-адаптер.

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strconv"
	"strings"
	"sync"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/device"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun/netstack"
)

type awgTunnel struct {
	addr netip.Addr // адрес клиента в туннеле (первый IPv4 из Address)
	dev  *device.Device
	tnet *netstack.Net
	ln   net.Listener
	wg   sync.WaitGroup
}

var (
	awgMu  sync.Mutex
	awgCur *awgTunnel
)

// StartAWG поднимает туннель по тексту .conf и возвращает порт локального SOCKS5.
func StartAWG(conf string) (int32, error) {
	awgMu.Lock()
	defer awgMu.Unlock()
	if awgCur != nil {
		awgCur.close()
		awgCur = nil
	}
	cfg, err := parseAWGConf(conf)
	if err != nil {
		return 0, err
	}
	uapi, err := cfg.toUAPI()
	if err != nil {
		return 0, err
	}
	tunDev, tnet, err := netstack.CreateNetTUN(cfg.addresses, cfg.dns, cfg.mtu)
	if err != nil {
		return 0, fmt.Errorf("сетевой стек AmneziaWG: %w", err)
	}
	logger := &device.Logger{Verbosef: device.DiscardLogf, Errorf: func(format string, args ...any) {
		logf("awg: "+format, args...)
	}}
	dev := device.NewDevice(tunDev, conn.NewDefaultBind(), logger)
	if err := dev.IpcSet(uapi); err != nil {
		dev.Close()
		return 0, fmt.Errorf("настройки AmneziaWG: %w", err)
	}
	if err := dev.Up(); err != nil {
		dev.Close()
		return 0, fmt.Errorf("запуск AmneziaWG: %w", err)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		dev.Close()
		return 0, err
	}
	t := &awgTunnel{addr: cfg.addresses[0], dev: dev, tnet: tnet, ln: ln}
	for _, a := range cfg.addresses {
		if a.Is4() {
			t.addr = a
			break
		}
	}
	t.wg.Add(1)
	go t.serve()
	awgCur = t
	return int32(ln.Addr().(*net.TCPAddr).Port), nil
}

// StopAWG останавливает туннель AmneziaWG (если он запущен).
func StopAWG() {
	awgMu.Lock()
	defer awgMu.Unlock()
	if awgCur != nil {
		awgCur.close()
		awgCur = nil
	}
}

func (t *awgTunnel) close() {
	_ = t.ln.Close()
	t.dev.Close()
	t.wg.Wait()
}

// ====================================================================== .conf → UAPI

type awgConfig struct {
	addresses []netip.Addr
	dns       []netip.Addr
	mtu       int
	iface     [][2]string // порядок важен только для читаемости
	peers     []map[string]string
}

func parseAWGConf(text string) (*awgConfig, error) {
	cfg := &awgConfig{mtu: 1280}
	var section string
	var peer map[string]string
	sc := bufio.NewScanner(strings.NewReader(text))
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if i := strings.IndexByte(line, '#'); i >= 0 {
			line = strings.TrimSpace(line[:i])
		}
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.ToLower(strings.Trim(line, "[]"))
			if section == "peer" {
				peer = map[string]string{}
				cfg.peers = append(cfg.peers, peer)
			}
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k, v = strings.ToLower(strings.TrimSpace(k)), strings.TrimSpace(v)
		switch section {
		case "interface":
			switch k {
			case "address":
				for _, a := range splitList(v) {
					p, err := netip.ParsePrefix(a)
					if err != nil {
						addr, err2 := netip.ParseAddr(a)
						if err2 != nil {
							return nil, fmt.Errorf("неверный Address: %s", a)
						}
						p = netip.PrefixFrom(addr, addr.BitLen())
					}
					cfg.addresses = append(cfg.addresses, p.Addr())
				}
			case "dns":
				for _, a := range splitList(v) {
					if addr, err := netip.ParseAddr(a); err == nil {
						cfg.dns = append(cfg.dns, addr)
					}
				}
			case "mtu":
				if n, err := strconv.Atoi(v); err == nil && n > 0 {
					cfg.mtu = n
				}
			case "table", "preup", "postup", "predown", "postdown", "saveconfig", "listenport":
				// к туннелю в приложении не относится
			default:
				cfg.iface = append(cfg.iface, [2]string{k, v})
			}
		case "peer":
			if peer != nil {
				peer[k] = v
			}
		}
	}
	if len(cfg.addresses) == 0 {
		return nil, errors.New("в конфиге нет Address")
	}
	if len(cfg.peers) == 0 {
		return nil, errors.New("в конфиге нет [Peer]")
	}
	if len(cfg.dns) == 0 {
		cfg.dns = []netip.Addr{netip.MustParseAddr("1.1.1.1")}
	}
	return cfg, nil
}

func splitList(v string) []string {
	var out []string
	for _, s := range strings.Split(v, ",") {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func keyHex(b64 string) (string, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(b64))
	if err != nil || len(raw) != 32 {
		return "", fmt.Errorf("неверный ключ %q", b64)
	}
	return hex.EncodeToString(raw), nil
}

func boolUAPI(v string) string {
	switch strings.ToLower(v) {
	case "on", "true", "1", "yes":
		return "true"
	}
	return "false"
}

// Параметры [Interface], которые передаются движку как есть (названия — как в UAPI).
var awgPassthrough = map[string]string{
	"jc": "jc", "jmin": "jmin", "jmax": "jmax", "s1": "s1", "s2": "s2", "s3": "s3", "s4": "s4",
	"h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4", "i1": "i1", "i2": "i2", "i3": "i3", "i4": "i4", "i5": "i5",
	"contentpaddingaddition": "content_padding_addition", "rekeyaftertime": "rekey_after_time",
	"rekeytimeout": "rekey_timeout", "rejectaftertime": "reject_after_time",
	"keepalivetimeout": "keepalive_timeout", "maxhandshakeattempts": "max_handshake_attempts",
}

func (c *awgConfig) toUAPI() (string, error) {
	var b strings.Builder
	for _, kv := range c.iface {
		k, v := kv[0], kv[1]
		switch {
		case k == "privatekey":
			h, err := keyHex(v)
			if err != nil {
				return "", fmt.Errorf("PrivateKey: %w", err)
			}
			fmt.Fprintf(&b, "private_key=%s\n", h)
		case k == "headerprotectionkey":
			h, err := keyHex(v)
			if err != nil {
				return "", fmt.Errorf("HeaderProtectionKey: %w", err)
			}
			fmt.Fprintf(&b, "header_protection_key=%s\n", h)
		case k == "randomtrailers":
			fmt.Fprintf(&b, "random_trailers=%s\n", boolUAPI(v))
		case k == "disablecookies":
			fmt.Fprintf(&b, "disable_cookies=%s\n", boolUAPI(v))
		case awgPassthrough[k] != "":
			fmt.Fprintf(&b, "%s=%s\n", awgPassthrough[k], v)
		}
	}
	b.WriteString("replace_peers=true\n")
	for _, p := range c.peers {
		pub, err := keyHex(p["publickey"])
		if err != nil {
			return "", fmt.Errorf("PublicKey: %w", err)
		}
		fmt.Fprintf(&b, "public_key=%s\n", pub)
		if psk := p["presharedkey"]; psk != "" {
			h, err := keyHex(psk)
			if err != nil {
				return "", fmt.Errorf("PresharedKey: %w", err)
			}
			fmt.Fprintf(&b, "preshared_key=%s\n", h)
		}
		if ep := p["endpoint"]; ep != "" {
			resolved, err := resolveEndpoint(ep)
			if err != nil {
				return "", err
			}
			fmt.Fprintf(&b, "endpoint=%s\n", resolved)
		}
		ka := p["persistentkeepalive"]
		if ka == "" || strings.EqualFold(ka, "off") {
			ka = "0"
		}
		fmt.Fprintf(&b, "persistent_keepalive_interval=%s\n", ka)
		b.WriteString("replace_allowed_ips=true\n")
		for _, a := range splitList(p["allowedips"]) {
			fmt.Fprintf(&b, "allowed_ip=%s\n", a)
		}
	}
	return b.String(), nil
}

func resolveEndpoint(ep string) (string, error) {
	host, port, err := net.SplitHostPort(ep)
	if err != nil {
		return "", fmt.Errorf("неверный Endpoint %q", ep)
	}
	if addr, err := netip.ParseAddr(host); err == nil {
		return netip.AddrPortFrom(addr, mustPort(port)).String(), nil
	}
	ips, err := net.DefaultResolver.LookupNetIP(context.Background(), "ip4", host)
	if err != nil || len(ips) == 0 {
		return "", fmt.Errorf("не удалось узнать адрес сервера %s: %v", host, err)
	}
	return netip.AddrPortFrom(ips[0].Unmap(), mustPort(port)).String(), nil
}

func mustPort(s string) uint16 {
	n, _ := strconv.Atoi(s)
	return uint16(n)
}

// EndpointHost — адрес сервера из .conf (чтобы sing-box пускал его мимо туннеля).
func EndpointHost(conf string) string {
	cfg, err := parseAWGConf(conf)
	if err != nil || len(cfg.peers) == 0 {
		return ""
	}
	resolved, err := resolveEndpoint(cfg.peers[0]["endpoint"])
	if err != nil {
		return ""
	}
	ap, err := netip.ParseAddrPort(resolved)
	if err != nil {
		return ""
	}
	return ap.Addr().String()
}
