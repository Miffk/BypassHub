package bhcore

// Ключи VPN: VLESS (vless://), AmneziaVPN (vpn://), конфиг AmneziaWG (.conf), подписки.
// Та же логика, что в BypassHub для Windows (bypasshub/vpn.py, awg.py) и в Android-версии (Keys.kt);
// на Go — чтобы ею пользовалась iOS-версия.

import (
	"bytes"
	"compress/zlib"
	"crypto/sha1"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Profile — сервер в списке: kind = vless (Data — ключ vless://) или awg (Data — текст .conf).
type Profile struct {
	ID      string `json:"id"`
	Kind    string `json:"kind"`
	Name    string `json:"name"`
	Data    string `json:"data"`
	Sub     string `json:"sub,omitempty"`
	Summary string `json:"summary,omitempty"`
}

type vlessKey struct {
	URI, Name, Server   string
	Port                int
	UUID                string
	Security, Transport string
	Flow, SNI, FP       string
	PublicKey, ShortID  string
	ALPN                []string
	Insecure            bool
	Path, Host, Service string
	// Hysteria2 (Protocol = "hysteria2"): пароль — в UUID; маскировка salamander, «прыжки» по портам, скорость
	Protocol         string
	ObfsPassword     string
	ServerPorts      string // диапазон портов, например 20000-50000
	HopInterval      int    // с
	UpMbps, DownMbps int
}

var (
	utlsFingerprints = map[string]bool{"chrome": true, "firefox": true, "edge": true, "safari": true, "360": true,
		"qq": true, "ios": true, "android": true, "random": true, "randomized": true}
	vlessTransports = map[string]bool{"tcp": true, "raw": true, "ws": true, "grpc": true, "http": true, "h2": true,
		"httpupgrade": true}
	reIPv4 = regexp.MustCompile(`^\d{1,3}(\.\d{1,3}){3}$`)
)

func isIP(s string) bool {
	return reIPv4.MatchString(s) || (strings.Contains(s, ":") && !strings.Contains(s, "."))
}

func urlDecode(s string) string {
	if v, err := url.QueryUnescape(strings.ReplaceAll(s, "+", "%2B")); err == nil {
		return v
	}
	return s
}

func (k *vlessKey) summary() string {
	if k.Protocol == "hysteria2" {
		s := fmt.Sprintf("%s:%d · Hysteria2", k.Server, k.Port)
		if k.ObfsPassword != "" {
			s += " · salamander"
		}
		return s
	}
	sec := map[string]string{"reality": "Reality", "tls": "TLS", "none": "без TLS"}[k.Security]
	if sec == "" {
		sec = k.Security
	}
	tr := map[string]string{"tcp": "TCP", "ws": "WebSocket", "grpc": "gRPC", "http": "HTTP/2",
		"httpupgrade": "HTTPUpgrade"}[k.Transport]
	if tr == "" {
		tr = k.Transport
	}
	return fmt.Sprintf("%s:%d · %s · %s", k.Server, k.Port, sec, tr)
}

var reLeadInt = regexp.MustCompile(`^\s*(\d+)`)

func leadInt(s string) int {
	if m := reLeadInt.FindStringSubmatch(s); m != nil {
		n, _ := strconv.Atoi(m[1])
		return n
	}
	return 0
}

// isKeyLine — ключ сервера для sing-box: vless:// или hysteria2:// (hy2://).
func isKeyLine(line string) bool {
	l := strings.ToLower(line)
	return strings.HasPrefix(l, "vless://") || strings.HasPrefix(l, "hysteria2://") || strings.HasPrefix(l, "hy2://")
}

// parseHysteria2 — ключ hysteria2://пароль@host:port/?sni=…&obfs=salamander&obfs-password=…#имя
// (порт может быть списком «443,20000-50000», mport — диапазон для «прыжков»).
func parseHysteria2(raw string) (*vlessKey, error) {
	body, fragment, _ := strings.Cut(raw, "#")
	rest := body[strings.Index(body, "://")+3:]
	netloc, tail, _ := strings.Cut(rest, "/")
	query := ""
	if i := strings.Index(netloc, "?"); i >= 0 {
		netloc, query = netloc[:i], netloc[i+1:]
	} else if _, q, ok := strings.Cut(tail, "?"); ok {
		query = q
	}
	at := strings.LastIndex(netloc, "@")
	if at <= 0 {
		return nil, errors.New("В ключе Hysteria2 нет пароля")
	}
	auth, hostPort := netloc[:at], netloc[at+1:]
	server, ports := hostPort, ""
	if strings.HasPrefix(hostPort, "[") {
		if end := strings.Index(hostPort, "]"); end > 0 {
			server, ports = hostPort[1:end], strings.TrimPrefix(hostPort[end+1:], ":")
		}
	} else if i := strings.Index(hostPort, ":"); i >= 0 {
		server, ports = hostPort[:i], hostPort[i+1:]
	}
	if ports == "" {
		ports = "443"
	}
	q := map[string]string{}
	for _, part := range strings.Split(query, "&") {
		if part == "" {
			continue
		}
		key, val, _ := strings.Cut(part, "=")
		q[urlDecode(key)] = strings.TrimSpace(urlDecode(val))
	}
	port := leadInt(strings.FieldsFunc(ports, func(r rune) bool { return r == ',' || r == '-' })[0])
	hop := q["mport"]
	if hop == "" && strings.ContainsAny(ports, ",-") {
		if _, after, ok := strings.Cut(ports, ","); ok {
			hop = after
		} else {
			hop = ports
		}
	}
	if server == "" || port <= 0 || port > 65535 {
		return nil, errors.New("В ключе Hysteria2 нет адреса сервера или порта")
	}
	obfs := q["obfs"]
	if obfs != "" && !strings.EqualFold(obfs, "salamander") {
		return nil, fmt.Errorf("Маскировка Hysteria2 «%s» не поддерживается", obfs)
	}
	k := &vlessKey{URI: raw, Server: server, Port: port, UUID: urlDecode(auth), Security: "tls",
		Protocol: "hysteria2", ServerPorts: strings.ReplaceAll(hop, ":", "-"), HopInterval: leadInt(q["hopinterval"]),
		UpMbps: leadInt(q["upmbps"]), DownMbps: leadInt(q["downmbps"])}
	k.SNI = q["sni"]
	if k.SNI == "" {
		k.SNI = q["peer"]
	}
	if obfs != "" {
		k.ObfsPassword = q["obfs-password"]
	}
	for _, a := range strings.Split(q["alpn"], ",") {
		if a = strings.TrimSpace(a); a != "" {
			k.ALPN = append(k.ALPN, a)
		}
	}
	ins := strings.ToLower(q["insecure"])
	k.Insecure = ins == "1" || ins == "true"
	k.Name = strings.TrimSpace(urlDecode(fragment))
	if k.Name == "" {
		k.Name = fmt.Sprintf("%s:%d", k.Server, k.Port)
	}
	return k, nil
}

func parseVless(text string) (*vlessKey, error) {
	raw := strings.TrimSpace(text)
	if l := strings.ToLower(raw); strings.HasPrefix(l, "hysteria2://") || strings.HasPrefix(l, "hy2://") {
		return parseHysteria2(raw)
	}
	if !strings.HasPrefix(strings.ToLower(raw), "vless://") {
		return nil, errors.New("Это не ключ VLESS — он должен начинаться с vless://")
	}
	fragment, body := "", raw
	if i := strings.Index(raw, "#"); i >= 0 {
		fragment, body = urlDecode(raw[i+1:]), raw[:i]
	}
	noScheme := body[8:]
	authority := noScheme
	if i := strings.IndexAny(authority, "?/"); i >= 0 {
		authority = authority[:i]
	}
	query := ""
	if i := strings.Index(noScheme, "?"); i >= 0 {
		query = noScheme[i+1:]
	}
	at := strings.LastIndex(authority, "@")
	if at <= 0 {
		return nil, errors.New("В ключе нет идентификатора (UUID)")
	}
	k := &vlessKey{URI: raw, UUID: strings.TrimSpace(urlDecode(authority[:at]))}
	hostPort := authority[at+1:]
	portText := ""
	if strings.HasPrefix(hostPort, "[") {
		end := strings.Index(hostPort, "]")
		if end > 0 {
			k.Server = hostPort[1:end]
			portText = strings.TrimPrefix(hostPort[end+1:], ":")
		}
	} else if i := strings.LastIndex(hostPort, ":"); i >= 0 {
		k.Server, portText = hostPort[:i], hostPort[i+1:]
	} else {
		k.Server = hostPort
	}
	port, err := strconv.Atoi(portText)
	if err != nil || k.UUID == "" || k.Server == "" || port <= 0 || port > 65535 {
		return nil, errors.New("В ключе нет адреса сервера, порта или идентификатора (UUID)")
	}
	k.Port = port
	q := map[string]string{}
	for _, part := range strings.Split(query, "&") {
		if part == "" {
			continue
		}
		key, val, _ := strings.Cut(part, "=")
		q[urlDecode(key)] = strings.TrimSpace(urlDecode(val))
	}
	get := func(key, def string) string {
		if v, ok := q[key]; ok {
			return v
		}
		return def
	}
	if enc := strings.ToLower(get("encryption", "none")); enc != "" && enc != "none" {
		return nil, fmt.Errorf("Шифрование VLESS «%s» пока не поддерживается", enc)
	}
	tr := strings.ToLower(get("type", "tcp"))
	if tr == "" {
		tr = "tcp"
	}
	if tr == "xhttp" || tr == "splithttp" {
		return nil, errors.New("Ключи с транспортом XHTTP пока не поддерживаются. Попросите у VPN-сервиса ключ " +
			"с TCP (Reality), WebSocket или gRPC.")
	}
	if !vlessTransports[tr] {
		return nil, fmt.Errorf("Транспорт «%s» не поддерживается", tr)
	}
	switch tr {
	case "raw":
		tr = "tcp"
	case "h2":
		tr = "http"
	}
	if h := strings.ToLower(get("headerType", "none")); tr == "tcp" && h != "" && h != "none" {
		return nil, errors.New("Маскировка TCP под HTTP (headerType=http) не поддерживается")
	}
	k.Transport = tr
	k.Security = strings.ToLower(get("security", "none"))
	if k.Security == "" {
		k.Security = "none"
	}
	if k.Security != "none" && k.Security != "tls" && k.Security != "reality" {
		return nil, fmt.Errorf("Тип защиты «%s» не поддерживается", k.Security)
	}
	k.Flow = get("flow", "")
	if strings.HasPrefix(k.Flow, "xtls-rprx-vision") {
		k.Flow = "xtls-rprx-vision"
	} else if k.Flow != "" {
		return nil, fmt.Errorf("Режим flow «%s» не поддерживается", k.Flow)
	}
	k.SNI = get("sni", "")
	if k.SNI == "" {
		k.SNI = get("peer", "")
	}
	k.FP = strings.ToLower(get("fp", ""))
	k.PublicKey, k.ShortID = get("pbk", ""), get("sid", "")
	for _, a := range strings.Split(get("alpn", ""), ",") {
		if a = strings.TrimSpace(a); a != "" {
			k.ALPN = append(k.ALPN, a)
		}
	}
	ins := strings.ToLower(get("allowInsecure", get("insecure", "")))
	k.Insecure = ins == "1" || ins == "true"
	k.Path, k.Host, k.Service = get("path", ""), get("host", ""), get("serviceName", "")
	k.Name = strings.TrimSpace(fragment)
	if k.Name == "" {
		k.Name = fmt.Sprintf("%s:%d", k.Server, k.Port)
	}
	if k.Security == "reality" {
		if k.PublicKey == "" {
			return nil, errors.New("В ключе Reality нет открытого ключа сервера (pbk)")
		}
		if k.SNI == "" {
			return nil, errors.New("В ключе Reality нет имени сайта для маскировки (sni)")
		}
	}
	return k, nil
}

var reEarlyData = regexp.MustCompile(`[?&]ed=(\d+)`)

func (k *vlessKey) hysteria2Outbound(tag string) map[string]any {
	tls := map[string]any{"enabled": true}
	name := k.SNI
	if name == "" && !isIP(k.Server) {
		name = k.Server
	}
	if name != "" {
		tls["server_name"] = name
	}
	if len(k.ALPN) > 0 {
		tls["alpn"] = k.ALPN
	}
	if k.Insecure {
		tls["insecure"] = true
	}
	ob := map[string]any{"type": "hysteria2", "tag": tag, "server": k.Server, "server_port": k.Port,
		"password": k.UUID, "tls": tls}
	if k.ObfsPassword != "" {
		ob["obfs"] = map[string]any{"type": "salamander", "password": k.ObfsPassword}
	}
	var ports []string
	for _, p := range strings.Split(k.ServerPorts, ",") {
		if p = strings.ReplaceAll(strings.TrimSpace(p), "-", ":"); p != "" {
			if !strings.Contains(p, ":") {
				p += ":" + p
			}
			ports = append(ports, p)
		}
	}
	if len(ports) > 0 {
		ob["server_ports"] = ports
		if k.HopInterval > 0 {
			ob["hop_interval"] = fmt.Sprintf("%ds", k.HopInterval)
		}
	}
	if k.UpMbps > 0 {
		ob["up_mbps"] = k.UpMbps
	}
	if k.DownMbps > 0 {
		ob["down_mbps"] = k.DownMbps
	}
	return ob
}

// outbound — исходящее sing-box для этого сервера.
func (k *vlessKey) outbound(tag string) map[string]any {
	if k.Protocol == "hysteria2" {
		return k.hysteria2Outbound(tag)
	}
	ob := map[string]any{"type": "vless", "tag": tag, "server": k.Server, "server_port": k.Port, "uuid": k.UUID,
		"packet_encoding": "xudp"}
	if k.Flow != "" {
		ob["flow"] = k.Flow
	}
	if k.Security == "tls" || k.Security == "reality" {
		tls := map[string]any{"enabled": true}
		name := k.SNI
		if name == "" && !isIP(k.Server) {
			name = k.Server
		}
		if name != "" {
			tls["server_name"] = name
		}
		if len(k.ALPN) > 0 {
			tls["alpn"] = k.ALPN
		}
		if k.Insecure {
			tls["insecure"] = true
		}
		fp := ""
		if utlsFingerprints[k.FP] {
			fp = k.FP
		}
		if k.Security == "reality" {
			if fp == "" {
				fp = "chrome"
			}
			tls["utls"] = map[string]any{"enabled": true, "fingerprint": fp}
			tls["reality"] = map[string]any{"enabled": true, "public_key": k.PublicKey, "short_id": k.ShortID}
		} else if fp != "" {
			tls["utls"] = map[string]any{"enabled": true, "fingerprint": fp}
		}
		ob["tls"] = tls
	}
	switch k.Transport {
	case "ws":
		tr := map[string]any{"type": "ws"}
		p := k.Path
		if p == "" {
			p = "/"
		}
		if m := reEarlyData.FindStringSubmatch(p); m != nil {
			p = reEarlyData.ReplaceAllString(p, "")
			if p == "" {
				p = "/"
			}
			n, _ := strconv.Atoi(m[1])
			tr["max_early_data"] = n
			tr["early_data_header_name"] = "Sec-WebSocket-Protocol"
		}
		tr["path"] = p
		if k.Host != "" {
			tr["headers"] = map[string]any{"Host": k.Host}
		}
		ob["transport"] = tr
	case "grpc":
		ob["transport"] = map[string]any{"type": "grpc", "service_name": k.Service}
	case "http":
		p := k.Path
		if p == "" {
			p = "/"
		}
		tr := map[string]any{"type": "http", "path": p}
		var hosts []string
		for _, h := range strings.Split(k.Host, ",") {
			if h = strings.TrimSpace(h); h != "" {
				hosts = append(hosts, h)
			}
		}
		if len(hosts) > 0 {
			tr["host"] = hosts
		}
		ob["transport"] = tr
	case "httpupgrade":
		p := k.Path
		if p == "" {
			p = "/"
		}
		tr := map[string]any{"type": "httpupgrade", "path": p}
		if k.Host != "" {
			tr["host"] = k.Host
		}
		ob["transport"] = tr
	}
	return ob
}

// ---------------------------------------------------------------- AmneziaVPN vpn:// и .conf

type awgConf struct {
	Name, Conf, Endpoint, Address string
}

func (c *awgConf) summary() string {
	s := c.Endpoint + " · AmneziaWG"
	if c.Address != "" {
		s += " · " + c.Address
	}
	return s
}

func b64decode(text string) ([]byte, error) {
	compact := strings.Join(strings.Fields(text), "")
	for _, enc := range []*base64.Encoding{base64.RawURLEncoding, base64.URLEncoding, base64.StdEncoding,
		base64.RawStdEncoding} {
		if b, err := enc.DecodeString(compact); err == nil {
			return b, nil
		}
	}
	return base64.RawURLEncoding.DecodeString(strings.TrimRight(compact, "="))
}

func inflate(b []byte) []byte {
	r, err := zlib.NewReader(bytes.NewReader(b))
	if err != nil {
		return nil
	}
	defer r.Close()
	out, err := io.ReadAll(r)
	if err != nil {
		return nil
	}
	return out
}

var errBrokenAmnezia = errors.New("Ключ AmneziaVPN повреждён — скопируйте его целиком")

func decodeAmneziaKey(key string) (map[string]any, error) {
	body := strings.TrimSpace(key)
	if strings.HasPrefix(strings.ToLower(body), "vpn://") {
		body = body[6:]
	}
	raw, err := b64decode(body)
	if err != nil {
		return nil, errBrokenAmnezia
	}
	// qCompress: 4 байта длины + zlib; бывает и просто zlib или открытый JSON
	data := []byte(nil)
	if len(raw) > 4 {
		data = inflate(raw[4:])
	}
	if data == nil {
		data = inflate(raw)
	}
	if data == nil {
		data = raw
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, errBrokenAmnezia
	}
	return m, nil
}

var (
	reDNSTrail     = regexp.MustCompile(`(?im)^(\s*DNS\s*=.*?)[,\s]+$`)
	reMTU          = regexp.MustCompile(`(?im)^\s*MTU\s*=`)
	reInterfaceHdr = regexp.MustCompile(`(?im)^(\s*\[Interface\]\s*)$`)
	reSection      = regexp.MustCompile(`^\[(\w+)\]$`)
)

func str(m map[string]any, k string) string {
	switch v := m[k].(type) {
	case string:
		return v
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	}
	return ""
}

func confFromAmneziaKey(key string) (*awgConf, error) {
	data, err := decodeAmneziaKey(key)
	if err != nil {
		return nil, err
	}
	name := str(data, "description")
	if name == "" {
		name = str(data, "hostName")
	}
	if name == "" {
		name = "AmneziaWG"
	}
	dns1, dns2 := str(data, "dns1"), str(data, "dns2")
	if dns1 == "" {
		dns1 = "1.1.1.1"
	}
	containers, _ := data["containers"].([]any)
	for _, ci := range containers {
		container, ok := ci.(map[string]any)
		if !ok {
			continue
		}
		for proto, v := range container {
			value, ok := v.(map[string]any)
			if !ok {
				continue
			}
			lastRaw, ok := value["last_config"]
			if !ok {
				continue
			}
			var last map[string]any
			switch lr := lastRaw.(type) {
			case map[string]any:
				last = lr
			case string:
				if json.Unmarshal([]byte(lr), &last) != nil {
					continue
				}
			default:
				continue
			}
			conf := str(last, "config")
			if !strings.Contains(conf, "[Interface]") {
				continue
			}
			if proto != "awg" && proto != "wireguard" {
				return nil, fmt.Errorf("В ключе протокол «%s» — BypassHub поддерживает AmneziaWG и VLESS", proto)
			}
			conf = strings.ReplaceAll(strings.ReplaceAll(conf, "$PRIMARY_DNS", dns1), "$SECONDARY_DNS", dns2)
			conf = reDNSTrail.ReplaceAllString(conf, "$1")
			if mtu := strings.TrimSpace(str(last, "mtu")); mtu != "" && isDigits(mtu) && !reMTU.MatchString(conf) {
				if loc := reInterfaceHdr.FindStringIndex(conf); loc != nil {
					conf = conf[:loc[1]] + "\nMTU = " + mtu + conf[loc[1]:]
				}
			}
			return parseConf(conf, name)
		}
	}
	for _, k := range []string{"api_config", "auth_data", "api_key"} {
		if _, ok := data[k]; ok {
			return nil, errors.New("Это ключ подписки Amnezia Premium — в нём нет настроек сервера. " +
				"Подойдёт ключ от своего сервера (AmneziaVPN → «Поделиться»).")
		}
	}
	return nil, errors.New("В ключе нет настроек AmneziaWG. В AmneziaVPN откройте «Поделиться» и выберите AmneziaWG.")
}

func isDigits(s string) bool {
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return s != ""
}

func looksLikeConf(t string) bool {
	l := strings.ToLower(t)
	return strings.Contains(l, "[interface]") && strings.Contains(l, "[peer]")
}

func parseConf(text, name string) (*awgConf, error) {
	conf := strings.TrimSpace(strings.ReplaceAll(text, "\r\n", "\n")) + "\n"
	section := ""
	iface, peer := map[string]string{}, map[string]string{}
	peers := 0
	for _, rawLine := range strings.Split(conf, "\n") {
		line := rawLine
		if i := strings.Index(line, "#"); i >= 0 {
			line = line[:i]
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if m := reSection.FindStringSubmatch(line); m != nil {
			section = strings.ToLower(m[1])
			if section == "peer" {
				peers++
			}
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k, v = strings.ToLower(strings.TrimSpace(k)), strings.TrimSpace(v)
		if section == "interface" {
			iface[k] = v
		} else if section == "peer" && peers == 1 {
			peer[k] = v
		}
	}
	if iface["privatekey"] == "" {
		return nil, errors.New("В конфиге нет [Interface] с PrivateKey")
	}
	if peer["publickey"] == "" || peer["endpoint"] == "" {
		return nil, errors.New("В конфиге нет [Peer] с PublicKey и Endpoint")
	}
	ep := peer["endpoint"]
	name = strings.TrimSpace(name)
	if name == "" {
		name = ep
		if i := strings.LastIndex(ep, ":"); i > 0 {
			name = ep[:i]
		}
	}
	return &awgConf{Name: name, Conf: conf, Endpoint: ep, Address: iface["address"]}, nil
}

// ---------------------------------------------------------------- профили и разбор текста

func profileID(kind, data string) string {
	h := sha1.Sum([]byte(strings.TrimSpace(data)))
	return kind + ":" + hex.EncodeToString(h[:])[:12]
}

func vlessProfile(k *vlessKey, sub string) Profile {
	return Profile{ID: profileID("vless", k.URI), Kind: "vless", Name: k.Name, Data: k.URI, Sub: sub, Summary: k.summary()}
}

func awgProfile(c *awgConf) Profile {
	return Profile{ID: profileID("awg", c.Conf), Kind: "awg", Name: c.Name, Data: c.Conf, Summary: c.summary()}
}

// Адреса, на которых сервис присылает не сервер, а сообщение в названии «ключа» (Remnawave так отвечает,
// когда достигнут лимит устройств, программа не прислала код устройства, подписка истекла и т.п.)
var placeholderHosts = map[string]bool{"0.0.0.0": true, "127.0.0.1": true, "::": true, "::1": true, "localhost": true}

func isPlaceholder(k *vlessKey) bool {
	return placeholderHosts[strings.ToLower(k.Server)] || k.Port <= 1
}

const (
	brokenVless    = "vless?" // сервер VLESS, который не удалось разобрать
	brokenHysteria = "hysteria?"
)

// walkMaps — все объекты внутри JSON (для полей, место которых в разных версиях Xray разное).
func walkMaps(v any, fn func(map[string]any) bool) bool {
	switch x := v.(type) {
	case map[string]any:
		if fn(x) {
			return true
		}
		for _, c := range x {
			if walkMaps(c, fn) {
				return true
			}
		}
	case []any:
		for _, c := range x {
			if walkMaps(c, fn) {
				return true
			}
		}
	}
	return false
}

// xrayHysteriaLink — исходящее Hysteria (Xray 26+: protocol hysteria, version 2) → ключ hysteria2://.
func xrayHysteriaLink(ob map[string]any, name string, str func(map[string]any, string) string,
	obj func(map[string]any, string) map[string]any) string {
	settings := obj(ob, "settings")
	ss := obj(ob, "streamSettings")
	hs := obj(ss, "hysteriaSettings")
	srv := settings
	for _, key := range []string{"servers", "vnext"} {
		if list, _ := settings[key].([]any); len(list) > 0 {
			if m, _ := list[0].(map[string]any); m != nil {
				srv = m
			}
		}
	}
	version := leadInt(str(hs, "version"))
	if version == 0 {
		version = leadInt(str(settings, "version"))
	}
	if version != 0 && version != 2 {
		return ""
	}
	host, port := str(srv, "address"), leadInt(str(srv, "port"))
	auth := ""
	for _, v := range []string{str(hs, "auth"), str(hs, "password"), str(srv, "password"), str(settings, "password")} {
		if v != "" {
			auth = v
			break
		}
	}
	if auth == "" {
		if users, _ := srv["users"].([]any); len(users) > 0 {
			if u, _ := users[0].(map[string]any); u != nil {
				auth = str(u, "password")
			}
		}
	}
	if host == "" || port == 0 || auth == "" {
		return ""
	}
	tls := obj(ss, "tlsSettings")
	q := url.Values{}
	if v := str(tls, "serverName"); v != "" {
		q.Set("sni", v)
	}
	if b, _ := tls["allowInsecure"].(bool); b {
		q.Set("insecure", "1")
	}
	if alpn, _ := tls["alpn"].([]any); len(alpn) > 0 {
		parts := make([]string, 0, len(alpn))
		for _, a := range alpn {
			if s, ok := a.(string); ok {
				parts = append(parts, s)
			}
		}
		q.Set("alpn", strings.Join(parts, ","))
	}
	walkMaps(ss, func(m map[string]any) bool { // маскировка salamander: в finalmask/udpmasks или в настройках
		kind := strings.ToLower(str(m, "type"))
		if kind == "" {
			kind = strings.ToLower(str(m, "name"))
		}
		if kind != "salamander" {
			return false
		}
		pw := str(m, "password")
		if pw == "" {
			pw = str(obj(m, "settings"), "password")
		}
		if pw == "" {
			return false
		}
		q.Set("obfs", "salamander")
		q.Set("obfs-password", pw)
		return true
	})
	if q.Get("obfs") == "" {
		if pw := str(hs, "obfs"); pw != "" {
			q.Set("obfs", "salamander")
			q.Set("obfs-password", pw)
		}
	}
	hop := obj(hs, "udphop")
	if len(hop) == 0 {
		hop = obj(hs, "hop")
	}
	if p := str(hop, "port"); p != "" {
		q.Set("mport", strings.ReplaceAll(p, ":", "-"))
		if n := leadInt(str(hop, "interval")); n > 0 {
			q.Set("hopinterval", strconv.Itoa(n))
		}
	}
	for _, k := range []string{"up", "down"} {
		if n := leadInt(str(hs, k)); n > 0 {
			q.Set(k+"mbps", strconv.Itoa(n))
		}
	}
	hostPart := host
	if strings.Contains(host, ":") {
		hostPart = "[" + host + "]"
	}
	label := name
	if label == "" {
		label = host
	}
	return fmt.Sprintf("hysteria2://%s@%s:%d/?%s#%s", url.QueryEscape(auth), hostPart, port, q.Encode(),
		url.PathEscape(label))
}

var proxyProtocols = map[string]bool{"vmess": true, "trojan": true, "shadowsocks": true, "hysteria": true,
	"hysteria2": true, "tuic": true, "wireguard": true, "socks": true, "http": true}

// xrayJSONLinks — подписка в формате Xray JSON (так её отдают Happ и Streisand): из каждого конфига
// берётся исходящее VLESS и пересобирается в ссылку vless://. ok=false — это не JSON. skipped —
// протоколы конфигов без VLESS (их серверы пропущены).
func xrayJSONLinks(text string) (links, skipped []string, ok bool) {
	t := strings.TrimSpace(text)
	if !strings.HasPrefix(t, "[") && !strings.HasPrefix(t, "{") {
		return nil, nil, false
	}
	var data any
	if json.Unmarshal([]byte(t), &data) != nil {
		return nil, nil, false
	}
	configs, isList := data.([]any)
	if !isList {
		configs = []any{data}
	}
	str := func(m map[string]any, key string) string {
		if v, ok := m[key]; ok && v != nil {
			switch x := v.(type) {
			case string:
				return x
			case float64:
				return strconv.FormatFloat(x, 'f', -1, 64)
			case bool:
				return strconv.FormatBool(x)
			}
		}
		return ""
	}
	obj := func(m map[string]any, key string) map[string]any {
		v, _ := m[key].(map[string]any)
		if v == nil {
			v = map[string]any{}
		}
		return v
	}
	for _, c := range configs {
		cfg, _ := c.(map[string]any)
		if cfg == nil {
			continue
		}
		name := str(cfg, "remarks")
		outbounds, _ := cfg["outbounds"].([]any)
		found, other := false, ""
		for _, o := range outbounds {
			ob, _ := o.(map[string]any)
			if ob == nil {
				continue
			}
			proto := str(ob, "protocol")
			if proto == "hysteria" || proto == "hysteria2" {
				if link := xrayHysteriaLink(ob, name, str, obj); link != "" {
					links = append(links, link)
					found = true
					break
				}
				other = brokenHysteria
				continue
			}
			if proto != "vless" {
				if other == "" && proxyProtocols[proto] {
					other = proto
				}
				continue
			}
			settings := obj(ob, "settings")
			srv, user := settings, settings // упрощённая запись Xray 25+: адрес, порт и id прямо в settings
			if vnext, _ := settings["vnext"].([]any); len(vnext) > 0 {
				srv, _ = vnext[0].(map[string]any)
				user = nil
				if srv != nil {
					if users, _ := srv["users"].([]any); len(users) > 0 {
						user, _ = users[0].(map[string]any)
					}
				}
			}
			if srv == nil || user == nil {
				other = brokenVless
				continue
			}
			host, uid := str(srv, "address"), str(user, "id")
			port, err := strconv.Atoi(str(srv, "port"))
			if host == "" || uid == "" || err != nil {
				other = brokenVless
				continue
			}
			ss := obj(ob, "streamSettings")
			netw, sec := str(ss, "network"), str(ss, "security")
			if netw == "" {
				netw = "tcp"
			}
			if sec == "" {
				sec = "none"
			}
			enc := str(user, "encryption")
			if enc == "" {
				enc = "none"
			}
			q := url.Values{}
			set := func(k, v string) {
				if v != "" {
					q.Set(k, v)
				}
			}
			set("encryption", enc)
			set("type", netw)
			set("security", sec)
			set("flow", str(user, "flow"))
			switch sec {
			case "reality":
				r := obj(ss, "realitySettings")
				set("sni", str(r, "serverName"))
				set("pbk", str(r, "publicKey"))
				set("sid", str(r, "shortId"))
				set("fp", str(r, "fingerprint"))
				set("spx", str(r, "spiderX"))
			case "tls":
				tl := obj(ss, "tlsSettings")
				set("sni", str(tl, "serverName"))
				set("fp", str(tl, "fingerprint"))
				if alpn, _ := tl["alpn"].([]any); len(alpn) > 0 {
					parts := make([]string, 0, len(alpn))
					for _, a := range alpn {
						if s, ok := a.(string); ok {
							parts = append(parts, s)
						}
					}
					set("alpn", strings.Join(parts, ","))
				}
				if b, _ := tl["allowInsecure"].(bool); b {
					set("allowInsecure", "1")
				}
			}
			switch netw {
			case "ws":
				w := obj(ss, "wsSettings")
				set("path", str(w, "path"))
				h := str(obj(w, "headers"), "Host")
				if h == "" {
					h = str(w, "host")
				}
				set("host", h)
			case "grpc":
				set("serviceName", str(obj(ss, "grpcSettings"), "serviceName"))
			case "httpupgrade":
				h := obj(ss, "httpupgradeSettings")
				set("path", str(h, "path"))
				set("host", str(h, "host"))
			case "xhttp":
				x := obj(ss, "xhttpSettings")
				set("path", str(x, "path"))
				set("host", str(x, "host"))
			}
			hostPart := host
			if strings.Contains(host, ":") {
				hostPart = "[" + host + "]"
			}
			label := name
			if label == "" {
				label = host
			}
			links = append(links, fmt.Sprintf("vless://%s@%s:%d?%s#%s", url.PathEscape(uid), hostPart, port,
				q.Encode(), url.PathEscape(label)))
			found = true
			break // в конфиге одно исходящее «proxy», остальные — direct/block
		}
		if !found && other != "" {
			skipped = append(skipped, other)
		}
	}
	return links, skipped, true
}

// parseSubscriptionText — ключи VLESS из текста или ответа подписки (ссылки, base64 или Xray JSON).
// notices — сообщения сервиса вместо серверов (заглушки на 0.0.0.0), в ключи они не попадают.
func parseSubscriptionText(text, sub string) (keys []Profile, problems, notices []string) {
	t := text
	links, jsonSkipped, isJSON := xrayJSONLinks(t)
	if isJSON {
		t = strings.Join(links, "\n")
	}
	if !strings.Contains(t, "://") {
		if b, err := b64decode(t); err == nil && strings.Contains(string(b), "://") {
			t = string(b)
		}
	}
	other := 0
	for _, raw := range strings.Split(t, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || !strings.Contains(line, "://") {
			continue
		}
		if !isKeyLine(line) {
			other++
			continue
		}
		k, err := parseVless(line)
		if err != nil {
			name := line
			if i := strings.LastIndex(line, "#"); i >= 0 {
				name = urlDecode(line[i+1:])
			} else if len(name) > 40 {
				name = name[:40] + "…"
			}
			problems = append(problems, name+": "+err.Error())
			continue
		}
		if isPlaceholder(k) {
			notices = append(notices, k.Name)
			continue
		}
		keys = append(keys, vlessProfile(k, sub))
	}
	brokenV, brokenH := 0, 0
	kept := jsonSkipped[:0]
	for _, x := range jsonSkipped {
		switch x {
		case brokenVless:
			brokenV++
		case brokenHysteria:
			brokenH++
		default:
			kept = append(kept, x)
		}
	}
	jsonSkipped = kept
	if brokenV > 0 {
		problems = append(problems, fmt.Sprintf("не удалось разобрать серверов VLESS в формате Xray JSON: %d", brokenV))
	}
	if brokenH > 0 {
		problems = append(problems, fmt.Sprintf("не удалось разобрать серверов Hysteria в формате Xray JSON: %d", brokenH))
	}
	other += len(jsonSkipped)
	if other > 0 {
		kinds := ""
		if len(jsonSkipped) > 0 {
			seen := map[string]bool{}
			var uniq []string
			for _, s := range jsonSkipped {
				if !seen[s] {
					seen[s] = true
					uniq = append(uniq, s)
				}
			}
			sort.Strings(uniq)
			kinds = " (" + strings.Join(uniq, ", ") + ")"
		}
		problems = append(problems, fmt.Sprintf("пропущено ключей других типов (не VLESS и не Hysteria2)%s: %d", kinds, other))
	}
	return keys, problems, notices
}

// parseVlessList — то же для вставленного текста: сообщения сервиса идут в предупреждения.
func parseVlessList(text, sub string) ([]Profile, []string) {
	keys, problems, notices := parseSubscriptionText(text, sub)
	for _, n := range notices {
		problems = append(problems, "сервис подписки сообщает: «"+n+"»")
	}
	return keys, problems
}

// ParsedInput — результат разбора вставленного текста.
type ParsedInput struct {
	Profiles      []Profile `json:"profiles"`
	Subscriptions []string  `json:"subscriptions"`
	Problems      []string  `json:"problems"`
}

func parseInput(text string) ParsedInput {
	out := ParsedInput{Profiles: []Profile{}, Subscriptions: []string{}, Problems: []string{}}
	t := strings.TrimSpace(text)
	if t == "" {
		return out
	}
	if looksLikeConf(t) {
		if c, err := parseConf(t, ""); err == nil {
			out.Profiles = append(out.Profiles, awgProfile(c))
		} else {
			out.Problems = append(out.Problems, err.Error())
		}
		return out
	}
	var rest []string
	for _, raw := range strings.Split(t, "\n") {
		line := strings.TrimSpace(raw)
		low := strings.ToLower(line)
		switch {
		case strings.HasPrefix(low, "vpn://"):
			if c, err := confFromAmneziaKey(line); err == nil {
				out.Profiles = append(out.Profiles, awgProfile(c))
			} else {
				out.Problems = append(out.Problems, err.Error())
			}
		case strings.HasPrefix(low, "http://") || strings.HasPrefix(low, "https://"):
			out.Subscriptions = append(out.Subscriptions, line)
		case line != "":
			rest = append(rest, line)
		}
	}
	if len(rest) > 0 {
		keys, problems := parseVlessList(strings.Join(rest, "\n"), "")
		out.Profiles = append(out.Profiles, keys...)
		out.Problems = append(out.Problems, problems...)
		if len(keys) == 0 && len(problems) == 0 && len(out.Profiles) == 0 && len(out.Subscriptions) == 0 {
			out.Problems = append(out.Problems, "Не нашёл ключей. Подходят vless://…, hysteria2://…, vpn://… (AmneziaVPN), "+
				"конфиг AmneziaWG или ссылка на подписку.")
		}
	}
	return out
}

func toJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// ParseInput разбирает вставленный текст: ключи vless://, vpn://, конфиг AmneziaWG, ссылки на подписки.
// Возвращает JSON ParsedInput.
func ParseInput(text string) string { return toJSON(parseInput(text)) }

// ParseSubscription разбирает ответ подписки (ссылки, base64 или Xray JSON).
// Возвращает JSON {profiles, problems, notices}; notices — сообщения сервиса вместо серверов.
func ParseSubscription(body, url string) string {
	p, problems, notices := parseSubscriptionText(body, url)
	if p == nil {
		p = []Profile{}
	}
	if problems == nil {
		problems = []string{}
	}
	if notices == nil {
		notices = []string{}
	}
	return toJSON(map[string]any{"profiles": p, "problems": problems, "notices": notices})
}

// VlessEndpoint — адрес и порт сервера из ключа vless:// (для пинга); пусто, если ключ не разобрать.
func VlessEndpoint(uri string) string {
	k, err := parseVless(uri)
	if err != nil || k.Protocol == "hysteria2" { // Hysteria2 работает по UDP — TCP-пинг к нему ничего не скажет
		return ""
	}
	return net.JoinHostPort(k.Server, strconv.Itoa(k.Port))
}
