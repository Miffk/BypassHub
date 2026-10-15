package bhcore

import (
	"bytes"
	"compress/zlib"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"strings"
	"testing"
)

const (
	keyReality = "vless://11111111-2222-3333-4444-555555555555@nl.example.com:443?type=tcp&security=reality&pbk=Zk3T2lWJ2a6o8QIhZ8m7rQ6dR4y7vE1Lr4m2aGxXqFk&fp=chrome&sni=www.microsoft.com&sid=ab12&flow=xtls-rprx-vision#NL%20Reality"
	keyWS      = "vless://11111111-2222-3333-4444-555555555555@de.example.com:443?type=ws&security=tls&path=%2Fws%3Fed%3D2048&host=de.example.com&sni=de.example.com#DE%20WS"
	keyGRPC    = "vless://11111111-2222-3333-4444-555555555555@[2001:db8::1]:8443?type=grpc&security=tls&serviceName=grpc&sni=fi.example.com#FI"
	testConf   = "[Interface]\nPrivateKey = YNXtAzepDqRv9H52osJVDQnznT5AL11eh7Ow2sjmqVM=\nAddress = 10.8.1.2/32\nDNS = $PRIMARY_DNS, $SECONDARY_DNS\nJc = 4\nH1 = 1000-2000\n\n[Peer]\nPublicKey = 3kZ2bR8k6yQx3m0b5V3oVt4m9GZ1Yt5xkG2v8c0yN0Y=\nEndpoint = 192.0.2.10:51820\nAllowedIPs = 0.0.0.0/0\n"
)

func amneziaKey(t *testing.T) string {
	last, _ := json.Marshal(map[string]any{"config": testConf, "mtu": "1280"})
	data, _ := json.Marshal(map[string]any{"description": "Test NL", "dns1": "1.1.1.1", "dns2": "1.0.0.1",
		"containers": []any{map[string]any{"container": "amnezia-awg", "awg": map[string]any{"last_config": string(last)}}}})
	var z bytes.Buffer
	w := zlib.NewWriter(&z)
	w.Write(data)
	w.Close()
	raw := binary.BigEndian.AppendUint32(nil, uint32(len(data)))
	raw = append(raw, z.Bytes()...)
	return "vpn://" + base64.RawURLEncoding.EncodeToString(raw)
}

func TestParseInputAllKinds(t *testing.T) {
	var res ParsedInput
	text := strings.Join([]string{keyReality, keyWS, keyGRPC, amneziaKey(t), "https://sub.example.com/x",
		"vmess://abc", "vless://bad"}, "\n")
	if err := json.Unmarshal([]byte(ParseInput(text)), &res); err != nil {
		t.Fatal(err)
	}
	if len(res.Profiles) != 4 || len(res.Subscriptions) != 1 {
		t.Fatalf("профили %d, подписки %d, проблемы %v", len(res.Profiles), len(res.Subscriptions), res.Problems)
	}
	names := map[string]Profile{}
	for _, p := range res.Profiles {
		names[p.Name] = p
	}
	if names["NL Reality"].Kind != "vless" || names["Test NL"].Kind != "awg" || names["FI"].Summary == "" {
		t.Fatalf("профили: %+v", res.Profiles)
	}
	awg := names["Test NL"].Data
	if !strings.Contains(awg, "DNS = 1.1.1.1, 1.0.0.1") || !strings.Contains(awg, "MTU = 1280") {
		t.Fatalf("конфиг AmneziaWG: %q", awg)
	}
	if len(res.Problems) != 2 {
		t.Fatalf("проблемы: %v", res.Problems)
	}
	if VlessEndpoint(keyGRPC) != "[2001:db8::1]:8443" {
		t.Fatal(VlessEndpoint(keyGRPC))
	}
	// конфиг .conf целиком
	_ = json.Unmarshal([]byte(ParseInput(strings.ReplaceAll(testConf, "$PRIMARY_DNS, $SECONDARY_DNS", "1.1.1.1"))), &res)
	if len(res.Profiles) != 1 || res.Profiles[0].Name != "192.0.2.10" {
		t.Fatalf("conf: %+v", res)
	}
	// подписка в base64
	sub := base64.StdEncoding.EncodeToString([]byte(keyReality + "\n" + keyWS))
	var s struct{ Profiles []Profile }
	_ = json.Unmarshal([]byte(ParseSubscription(sub, "https://sub")), &s)
	if len(s.Profiles) != 2 || s.Profiles[0].Sub != "https://sub" {
		t.Fatalf("подписка: %+v", s)
	}
}

func TestOutboundsAndConfigs(t *testing.T) {
	var servers []map[string]any
	for _, k := range []string{keyReality, keyWS, keyGRPC} {
		vk, err := parseVless(k)
		if err != nil {
			t.Fatal(err)
		}
		servers = append(servers, vk.outbound(profileID("vless", k)))
	}
	ws := servers[1]["transport"].(map[string]any)
	if ws["path"] != "/ws" || ws["max_early_data"] != 2048 {
		t.Fatalf("ws: %v", ws)
	}
	vpn := buildVPNConfig(serverGroup(servers, autoTag), true, map[string]string{}, 9090, "s", nil)
	if err := CheckConfig(vpn); err != nil {
		t.Fatalf("VPN: %v\n%s", err, vpn)
	}
	awg := buildVPNConfig([]map[string]any{{"type": "socks", "tag": "proxy", "server": "127.0.0.1",
		"server_port": 1080, "version": "5"}}, false, nil, 9090, "s", []string{"192.0.2.10"})
	if err := CheckConfig(awg); err != nil {
		t.Fatalf("AWG: %v", err)
	}
	suffix, exact := parseDomains([]string{"youtube.com", "^dns.google", "# x", "bad", "*.Discord.com"})
	if len(suffix) != 2 || len(exact) != 1 {
		t.Fatalf("домены: %v %v", suffix, exact)
	}
	for _, m := range []string{BypassTCP, BypassRecord, BypassDisorder, BypassRecordDisorder} {
		for _, all := range []bool{false, true} {
			cfg := buildBypassConfig(suffix, exact, all, m, 9090, "s", 18555)
			if err := CheckConfig(cfg); err != nil {
				t.Fatalf("обход %s/%v: %v", m, all, err)
			}
		}
	}
	conf, ip, err := resolveAWGEndpoint(testConf)
	if err != nil || ip != "192.0.2.10" || !strings.Contains(conf, "Endpoint = 192.0.2.10:51820\n") {
		t.Fatalf("endpoint: %v %q", err, conf)
	}
}
