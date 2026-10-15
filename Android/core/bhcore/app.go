package bhcore

// Режимы целиком: VPN, обход блокировок, TG-прокси — от настроек до запущенного ядра.
// Та же логика, что в Android-версии (Config.kt, Bypass.kt, Rules.kt, BypassVpnService.kt), но на Go:
// так ею пользуется iOS-расширение, где удобнее один вызов Run, чем много мелких.

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	C "github.com/sagernet/sing-box/constant"
)

const (
	autoTag    = "auto"
	urltestURL = "https://www.gstatic.com/generate_204"
	rulesBase  = "https://raw.githubusercontent.com/hydraponique/roscomvpn-%s/release/sing-box/%s.srs"
	listsBase  = "https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/main/lists/"
	tgCFList   = "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt"

	BypassTCP            = "tcp"
	BypassRecord         = "record"
	BypassDisorder       = DesyncDisorder
	BypassRecordDisorder = DesyncRecordDisorder
)

var ruSuffixes = []string{"ru", "su", "xn--p1ai"}

// Базы раздельного туннелирования roscomvpn (тег → [тип, имя]).
var ruleSets = []struct{ Tag, Kind, Name string }{
	{"rc-youtube", "geosite", "youtube"},
	{"rc-telegram", "geosite", "telegram"},
	{"rc-github", "geosite", "github"},
	{"rc-google-play", "geosite", "google-play"},
	{"rc-category-ru", "geosite", "category-ru"},
	{"rc-whitelist", "geosite", "whitelist"},
	{"rc-direct-ip", "geoip", "direct"},
}

var (
	proxySets      = []string{"rc-youtube", "rc-telegram", "rc-github", "rc-google-play"}
	directSiteSets = []string{"rc-category-ru", "rc-whitelist"}
	directIPSets   = []string{"rc-direct-ip"}
	bypassLists    = []string{"list-general.txt", "list-google.txt"}
	bypassBuiltin  = []string{
		"youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "ggpht.com", "youtube-nocookie.com",
		"youtubei.googleapis.com", "youtube.googleapis.com", "yt3.googleusercontent.com", "jnn-pa.googleapis.com",
		"discord.com", "discord.gg", "discord.media", "discordapp.com", "discordapp.net", "discordcdn.com",
		"discord.app", "discord.dev", "discord.gift", "discordstatus.com", "dis.gd",
		"play.google.com", "twitch.tv", "live-video.net",
	}
)

// RunRequest — что запустить (JSON из приложения).
type RunRequest struct {
	Mode         string    `json:"mode"` // vpn | bypass | none (только TG-прокси)
	Profiles     []Profile `json:"profiles"`
	Selected     string    `json:"selected"` // "auto" или id сервера
	RuDirect     bool      `json:"ruDirect"`
	BypassMethod string    `json:"bypassMethod"`
	BypassAll    bool      `json:"bypassAll"`
	BypassUser   string    `json:"bypassUser"`
	TG           struct {
		Enabled bool   `json:"enabled"`
		Port    int    `json:"port"`
		Secret  string `json:"secret"`
		CF      bool   `json:"cf"`
	} `json:"tg"`
	DataDir string `json:"dataDir"` // куда скачивать базы и списки
}

type appState struct {
	mode, server, kind string
	apiPort            int
	apiSecret          string
	groupTags          []string
	names              map[string]string
}

var (
	appMu  sync.Mutex
	appCur appState
)

// ---------------------------------------------------------------- скачивание

func httpClient(timeout time.Duration) *http.Client { return &http.Client{Timeout: timeout} }

func download(url, dest string) error {
	resp, err := httpClient(30 * time.Second).Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	tmp := dest + ".part"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		return err
	}
	f.Close()
	return os.Rename(tmp, dest)
}

func fresh(path string, maxAge time.Duration) bool {
	st, err := os.Stat(path)
	return err == nil && st.Size() > 0 && time.Since(st.ModTime()) < maxAge
}

// updateRules — базы roscomvpn (недостающие или старше 3 дней); ошибки не критичны.
func updateRules(dir string) map[string]string {
	files := map[string]string{}
	for _, rs := range ruleSets {
		p := filepath.Join(dir, "rules", rs.Tag+".srs")
		if !fresh(p, 72*time.Hour) {
			if err := download(fmt.Sprintf(rulesBase, rs.Kind, rs.Name), p); err != nil {
				logf("Базы: не удалось скачать %s: %v", rs.Tag, err)
			}
		}
		if st, err := os.Stat(p); err == nil && st.Size() > 0 {
			files[rs.Tag] = p
		}
	}
	return files
}

// ---------------------------------------------------------------- VPN

func serverGroup(servers []map[string]any, selected string) []map[string]any {
	tags := make([]string, 0, len(servers))
	for _, s := range servers {
		tags = append(tags, s["tag"].(string))
	}
	sel := selected
	if sel != autoTag && !contains(tags, sel) {
		sel = tags[0]
	}
	out := append([]map[string]any{}, servers...)
	out = append(out,
		map[string]any{"type": "urltest", "tag": autoTag, "outbounds": tags, "url": urltestURL, "interval": "1m",
			"tolerance": 50, "idle_timeout": "30m"},
		map[string]any{"type": "selector", "tag": "proxy", "outbounds": append([]string{autoTag}, tags...),
			"default": sel, "interrupt_exist_connections": true})
	return out
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func tunInbound() map[string]any {
	// gvisor: весь TCP/UDP обрабатывается внутри ядра (см. Android: стек system/mixed не работает,
	// когда само приложение идёт мимо своего VPN)
	return map[string]any{"type": "tun", "tag": "tun-in", "address": []string{"172.19.0.1/30", "fdfe:dcba:9876::1/126"},
		"mtu": tunMTU(), "auto_route": true, "stack": "gvisor"}
}

func clashAPI(port int, secret string) map[string]any {
	return map[string]any{"clash_api": map[string]any{"external_controller": fmt.Sprintf("127.0.0.1:%d", port),
		"secret": secret}}
}

func buildVPNConfig(proxy []map[string]any, ruDirect bool, rules map[string]string, apiPort int, apiSecret string,
	bypass []string) string {
	has := map[string]bool{}
	if ruDirect {
		for t := range rules {
			has[t] = true
		}
	}
	filter := func(tags []string) []string {
		var out []string
		for _, t := range tags {
			if has[t] {
				out = append(out, t)
			}
		}
		return out
	}
	var cidrs []string
	for _, b := range bypass {
		switch {
		case strings.Contains(b, "/"):
			cidrs = append(cidrs, b)
		case strings.Contains(b, ":"):
			cidrs = append(cidrs, b+"/128")
		default:
			cidrs = append(cidrs, b+"/32")
		}
	}
	dnsRules := []any{}
	routeRules := []any{
		map[string]any{"action": "sniff"},
		map[string]any{"protocol": "dns", "action": "hijack-dns"},
	}
	if len(cidrs) > 0 {
		routeRules = append(routeRules, map[string]any{"ip_cidr": cidrs, "outbound": "direct"})
	}
	routeRules = append(routeRules, map[string]any{"ip_is_private": true, "outbound": "direct"})
	if ruDirect {
		if ps := filter(proxySets); len(ps) > 0 {
			dnsRules = append(dnsRules, map[string]any{"rule_set": ps, "server": "dns-remote"})
			routeRules = append(routeRules, map[string]any{"rule_set": ps, "outbound": "proxy"})
		}
		dnsRules = append(dnsRules, map[string]any{"domain_suffix": ruSuffixes, "server": "dns-direct"})
		routeRules = append(routeRules, map[string]any{"domain_suffix": ruSuffixes, "outbound": "direct"})
		if ss := filter(directSiteSets); len(ss) > 0 {
			dnsRules = append(dnsRules, map[string]any{"rule_set": ss, "server": "dns-direct"})
			routeRules = append(routeRules, map[string]any{"rule_set": ss, "outbound": "direct"})
		}
		if is := filter(directIPSets); len(is) > 0 {
			routeRules = append(routeRules, map[string]any{"rule_set": is, "outbound": "direct"})
		}
	}
	route := map[string]any{"rules": routeRules, "final": "proxy", "auto_detect_interface": false,
		"default_domain_resolver": "dns-direct"}
	var sets []any
	for _, rs := range ruleSets {
		if has[rs.Tag] {
			sets = append(sets, map[string]any{"type": "local", "tag": rs.Tag, "format": "binary", "path": rules[rs.Tag]})
		}
	}
	if len(sets) > 0 {
		route["rule_set"] = sets
	}
	outbounds := append(append([]map[string]any{}, proxy...), map[string]any{"type": "direct", "tag": "direct"})
	cfg := map[string]any{
		"log": map[string]any{"level": "info", "timestamp": true},
		"dns": map[string]any{
			"servers": []any{
				map[string]any{"type": "https", "tag": "dns-remote", "server": "1.1.1.1", "detour": "proxy"},
				map[string]any{"type": "udp", "tag": "dns-direct", "server": "77.88.8.8"},
			},
			"rules": dnsRules, "final": "dns-remote", "strategy": "ipv4_only",
		},
		"inbounds":     []any{tunInbound()},
		"outbounds":    outbounds,
		"route":        route,
		"experimental": clashAPI(apiPort, apiSecret),
	}
	return toJSON(cfg)
}

// ---------------------------------------------------------------- обход блокировок

// parseDomains — строки списка → (домены с поддоменами, точные). «^домен» в списках zapret — без поддоменов.
func parseDomains(lines []string) ([]string, []string) {
	re := regexp.MustCompile(`^[a-z0-9.-]+\.[a-z0-9-]{2,}$`)
	suffix, exact := []string{}, []string{}
	seenS, seenE := map[string]bool{}, map[string]bool{}
	for _, raw := range lines {
		s := raw
		if i := strings.Index(s, "#"); i >= 0 {
			s = s[:i]
		}
		s = strings.ToLower(strings.TrimSpace(s))
		if s == "" {
			continue
		}
		s = strings.TrimPrefix(strings.TrimPrefix(s, "http://"), "https://")
		if i := strings.Index(s, "/"); i >= 0 {
			s = s[:i]
		}
		s = strings.TrimSuffix(strings.TrimPrefix(s, "*."), ".")
		isExact := strings.HasPrefix(s, "^")
		s = strings.TrimPrefix(strings.TrimPrefix(s, "^"), ".")
		if !re.MatchString(s) {
			continue
		}
		if isExact {
			if !seenE[s] {
				seenE[s] = true
				exact = append(exact, s)
			}
		} else if !seenS[s] {
			seenS[s] = true
			suffix = append(suffix, s)
		}
	}
	var ex []string
	for _, e := range exact {
		if !seenS[e] {
			ex = append(ex, e)
		}
	}
	if ex == nil {
		ex = []string{}
	}
	return suffix, ex
}

func bypassDomains(dir, user string) ([]string, []string) {
	var lines []string
	for _, name := range bypassLists {
		p := filepath.Join(dir, "bypass", name)
		if !fresh(p, 24*time.Hour) {
			if err := download(listsBase+name, p); err != nil {
				logf("Обход: не удалось скачать %s: %v", name, err)
			}
		}
		if b, err := os.ReadFile(p); err == nil {
			lines = append(lines, strings.Split(string(b), "\n")...)
		}
	}
	lines = append(lines, bypassBuiltin...)
	lines = append(lines, strings.Split(user, "\n")...)
	return parseDomains(lines)
}

func buildBypassConfig(suffix, exact []string, allSites bool, method string, apiPort int, apiSecret string,
	desyncPort int) string {
	match := func(m map[string]any) map[string]any {
		if len(suffix) > 0 {
			m["domain_suffix"] = suffix
		}
		if len(exact) > 0 {
			m["domain"] = exact
		}
		return m
	}
	useDesync := (method == BypassDisorder || method == BypassRecordDisorder) && desyncPort > 0
	fragment := func() map[string]any {
		if useDesync {
			return map[string]any{"outbound": "desync"}
		}
		m := map[string]any{"action": "route-options"}
		if method == BypassRecord {
			m["tls_record_fragment"] = true
		} else {
			m["tls_fragment"] = true
		}
		return m
	}
	rules := []any{
		map[string]any{"action": "sniff"},
		map[string]any{"protocol": "dns", "action": "hijack-dns"},
		map[string]any{"ip_is_private": true, "outbound": "direct"},
	}
	if allSites {
		f := fragment()
		f["protocol"] = "tls"
		rules = append(rules,
			map[string]any{"domain_suffix": ruSuffixes, "outbound": "direct"},
			map[string]any{"network": "udp", "port": 443, "action": "reject"},
			f)
	} else {
		rules = append(rules,
			match(map[string]any{"network": "udp", "port": 443, "action": "reject"}),
			match(fragment()))
	}
	outbounds := []any{map[string]any{"type": "direct", "tag": "direct"}}
	if useDesync {
		outbounds = append(outbounds, map[string]any{"type": "socks", "tag": "desync", "server": "127.0.0.1",
			"server_port": desyncPort, "version": "5"})
	}
	cfg := map[string]any{
		"log": map[string]any{"level": "info", "timestamp": true},
		"dns": map[string]any{
			"servers": []any{
				map[string]any{"type": "udp", "tag": "dns-remote", "server": "8.8.8.8"},
				map[string]any{"type": "udp", "tag": "dns-direct", "server": "77.88.8.8"},
			},
			"rules":    []any{map[string]any{"domain_suffix": ruSuffixes, "server": "dns-direct"}},
			"final":    "dns-remote",
			"strategy": "ipv4_only",
		},
		"inbounds":  []any{tunInbound()},
		"outbounds": outbounds,
		"route": map[string]any{"rules": rules, "final": "direct", "auto_detect_interface": false,
			"default_domain_resolver": "dns-direct"},
		"experimental": clashAPI(apiPort, apiSecret),
	}
	return toJSON(cfg)
}

// ---------------------------------------------------------------- запуск

func freePort() int {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0
	}
	defer ln.Close()
	return ln.Addr().(*net.TCPAddr).Port
}

func randomSecret() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

var reEndpoint = regexp.MustCompile(`(?im)^([ \t]*Endpoint[ \t]*=[ \t]*)(.+?):(\d+)[ \t]*$`)

// resolveAWGEndpoint подставляет в конфиг IP сервера вместо имени: так ядру не нужен DNS,
// а адрес сервера можно пустить мимо туннеля.
func resolveAWGEndpoint(conf string) (string, string, error) {
	m := reEndpoint.FindStringSubmatchIndex(conf)
	if m == nil {
		return "", "", errors.New("В конфиге нет Endpoint")
	}
	host := strings.Trim(conf[m[4]:m[5]], "[] ")
	ip := ""
	if parsed := net.ParseIP(host); parsed != nil && parsed.To4() != nil {
		ip = parsed.String()
	} else {
		ips, err := net.LookupIP(host)
		if err != nil {
			return "", "", fmt.Errorf("Не удалось узнать адрес сервера %s: %v", host, err)
		}
		for _, a := range ips {
			if a.To4() != nil {
				ip = a.String()
				break
			}
		}
		if ip == "" {
			return "", "", fmt.Errorf("У сервера %s нет адреса IPv4", host)
		}
	}
	fixed := conf[:m[3]] + ip + ":" + conf[m[6]:m[7]] + conf[m[1]:]
	return fixed, ip, nil
}

// tunMTU — MTU туннеля: на iOS выше 4064 utun заметно тормозит (так же в sing-box), на Android — 9000.
func tunMTU() int {
	if C.IsIos {
		return 4064
	}
	return 9000
}

type tgLogAdapter struct{ p Platform }

func (l tgLogAdapter) WriteLog(m string) {
	if l.p != nil {
		l.p.WriteLog(m)
	}
}

// Run запускает режим по JSON RunRequest: VPN (VLESS-группа или AmneziaWG), обход блокировок или
// ничего (mode = none), и TG-прокси, если он включён. Всё, что работало до этого, останавливается.
func Run(requestJSON string, platform Platform) error {
	var req RunRequest
	if err := json.Unmarshal([]byte(requestJSON), &req); err != nil {
		return fmt.Errorf("настройки: %v", err)
	}
	StopAll()
	logPlatform = platform
	if req.DataDir == "" {
		req.DataDir = workingPath
	}
	st := appState{mode: req.Mode, names: map[string]string{}}
	for _, p := range req.Profiles {
		st.names[p.ID] = p.Name
	}
	var err error
	switch req.Mode {
	case "vpn":
		err = runVPN(&req, &st, platform)
	case "bypass":
		err = runBypass(&req, &st, platform)
	}
	if err != nil {
		StopAll()
		return err
	}
	if req.TG.Enabled {
		cf := ""
		if req.TG.CF {
			if resp, err := httpClient(8 * time.Second).Get(tgCFList); err == nil {
				b, _ := io.ReadAll(resp.Body)
				resp.Body.Close()
				cf = string(b)
			}
		}
		if err := StartTG(fmt.Sprintf("127.0.0.1:%d", req.TG.Port), req.TG.Secret, cf, req.TG.CF,
			tgLogAdapter{platform}); err != nil {
			logf("TG: не запустился: %v", err)
		}
	}
	appMu.Lock()
	appCur = st
	appMu.Unlock()
	return nil
}

func runVPN(req *RunRequest, st *appState, platform Platform) error {
	var vless []Profile
	keys := map[string]*vlessKey{}
	for _, p := range req.Profiles {
		if p.Kind == "vless" {
			if k, err := parseVless(p.Data); err == nil {
				vless = append(vless, p)
				keys[p.ID] = k
			}
		}
	}
	if len(req.Profiles) == 0 {
		return errors.New("Сначала добавьте свой сервер: ключ vless://… или ключ AmneziaVPN vpn://…")
	}
	auto := req.Selected == autoTag && len(vless) >= 2
	var active *Profile
	if !auto {
		for i := range req.Profiles {
			if req.Profiles[i].ID == req.Selected {
				active = &req.Profiles[i]
			}
		}
		if active == nil && len(vless) > 0 {
			active = &vless[0]
		}
		if active == nil {
			active = &req.Profiles[0]
		}
	}
	var proxy []map[string]any
	var bypass []string
	if active != nil && active.Kind == "awg" {
		c, err := parseConf(active.Data, active.Name)
		if err != nil {
			return err
		}
		conf, ip, err := resolveAWGEndpoint(c.Conf)
		if err != nil {
			return err
		}
		port, err := StartAWG(conf)
		if err != nil {
			return err
		}
		proxy = []map[string]any{{"type": "socks", "tag": "proxy", "server": "127.0.0.1", "server_port": port,
			"version": "5"}}
		bypass = []string{ip}
		st.kind, st.server = "awg", active.Name
	} else {
		if len(vless) == 0 {
			return errors.New("Нет рабочих ключей VLESS")
		}
		var servers []map[string]any
		for _, p := range vless {
			servers = append(servers, keys[p.ID].outbound(p.ID))
			st.groupTags = append(st.groupTags, p.ID)
		}
		sel := autoTag
		if active != nil {
			sel = active.ID
		}
		proxy = serverGroup(servers, sel)
		st.kind = "vless"
		if sel == autoTag {
			st.server = "Авто — лучший сервер"
		} else {
			st.server = active.Name
		}
	}
	rules := map[string]string{}
	if req.RuDirect {
		rules = updateRules(req.DataDir)
	}
	st.apiPort, st.apiSecret = freePort(), randomSecret()
	cfg := buildVPNConfig(proxy, req.RuDirect, rules, st.apiPort, st.apiSecret, bypass)
	logf("Запуск VPN: %s", st.server)
	return Start(cfg, platform)
}

func runBypass(req *RunRequest, st *appState, platform Platform) error {
	suffix, exact := bypassDomains(req.DataDir, req.BypassUser)
	method := req.BypassMethod
	if method == "" {
		method = BypassDisorder
	}
	desync := 0
	if method == BypassDisorder || method == BypassRecordDisorder {
		p, err := StartDesync(method)
		if err != nil {
			return err
		}
		desync = p
	}
	st.kind, st.server = "bypass", "Обход блокировок"
	st.apiPort, st.apiSecret = freePort(), randomSecret()
	cfg := buildBypassConfig(suffix, exact, req.BypassAll, method, st.apiPort, st.apiSecret, desync)
	logf("Запуск обхода: способ %s, сайтов в списке %d", method, len(suffix)+len(exact))
	return Start(cfg, platform)
}

// StopAll останавливает всё: ядро, AmneziaWG, приём обхода и TG-прокси.
func StopAll() {
	Stop()
	StopAWG()
	StopDesync()
	StopTG()
	appMu.Lock()
	appCur = appState{}
	appMu.Unlock()
}

// ---------------------------------------------------------------- состояние и серверы (API sing-box)

func apiCall(method, path string, body string, timeout time.Duration) ([]byte, error) {
	appMu.Lock()
	st := appCur
	appMu.Unlock()
	if st.apiPort == 0 {
		return nil, errors.New("ядро не запущено")
	}
	req, err := http.NewRequest(method, fmt.Sprintf("http://127.0.0.1:%d%s", st.apiPort, path), strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+st.apiSecret)
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := httpClient(timeout).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("HTTP %d %s", resp.StatusCode, string(b))
	}
	return b, nil
}

// State — JSON {mode, kind, server, selected, current, delays{id: мс}, tg, tgStats} для экрана.
func State() string {
	appMu.Lock()
	st := appCur
	appMu.Unlock()
	out := map[string]any{"mode": st.mode, "kind": st.kind, "server": st.server, "running": IsRunning(),
		"tg": TGRunning(), "tgStats": TGStats(), "delays": map[string]int{}}
	if st.kind == "vless" {
		if b, err := apiCall("GET", "/proxies", "", 3*time.Second); err == nil {
			var data struct {
				Proxies map[string]struct {
					Now     string `json:"now"`
					All     []string
					History []struct {
						Delay int `json:"delay"`
					} `json:"history"`
				} `json:"proxies"`
			}
			if json.Unmarshal(b, &data) == nil {
				sel := data.Proxies["proxy"].Now
				cur := sel
				if sel == autoTag {
					cur = data.Proxies[autoTag].Now
				}
				delays := map[string]int{}
				for _, tag := range st.groupTags {
					if h := data.Proxies[tag].History; len(h) > 0 && h[len(h)-1].Delay > 0 {
						delays[tag] = h[len(h)-1].Delay
					}
				}
				out["selected"], out["current"], out["delays"] = sel, cur, delays
				if name, ok := st.names[cur]; ok && cur != "" {
					out["currentName"] = name
				}
			}
		}
	}
	return toJSON(out)
}

// SelectServer переключает сервер VLESS на лету (id или "auto"). false — нужен перезапуск.
func SelectServer(id string) bool {
	appMu.Lock()
	st := appCur
	appMu.Unlock()
	if st.kind != "vless" || (id != autoTag && !contains(st.groupTags, id)) {
		return false
	}
	_, err := apiCall("PUT", "/proxies/proxy", toJSON(map[string]string{"name": id}), 3*time.Second)
	if err != nil {
		logf("Переключение не удалось: %v", err)
		return false
	}
	return true
}

// TestDelays — задержки серверов VLESS через сами серверы: JSON {id: мс}.
func TestDelays() string {
	b, err := apiCall("GET", "/group/"+autoTag+"/delay?url="+urltestURL+"&timeout=5000", "", 8*time.Second)
	if err != nil {
		return "{}"
	}
	var m map[string]int
	_ = json.Unmarshal(b, &m)
	out := map[string]int{}
	for k, v := range m {
		if v > 0 {
			out[k] = v
		}
	}
	return toJSON(out)
}

// TCPPing — время TCP-подключения к host:port в мс, -1 — не отвечает.
func TCPPing(hostport string) int {
	start := time.Now()
	c, err := net.DialTimeout("tcp", hostport, 4*time.Second)
	if err != nil {
		return -1
	}
	c.Close()
	return int(time.Since(start).Milliseconds())
}

// PingAll — TCP-пинг серверов VLESS параллельно: profilesJSON — список Profile, ответ — JSON {id: мс}.
func PingAll(profilesJSON string) string {
	var ps []Profile
	_ = json.Unmarshal([]byte(profilesJSON), &ps)
	var wg sync.WaitGroup
	var mu sync.Mutex
	out := map[string]int{}
	for _, p := range ps {
		if p.Kind != "vless" {
			continue
		}
		ep := VlessEndpoint(p.Data)
		if ep == "" {
			continue
		}
		wg.Add(1)
		go func(id, ep string) {
			defer wg.Done()
			ms := TCPPing(ep)
			mu.Lock()
			out[id] = ms
			mu.Unlock()
		}(p.ID, ep)
	}
	wg.Wait()
	return toJSON(out)
}
