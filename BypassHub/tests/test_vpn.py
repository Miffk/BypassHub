import base64
import json
import zlib

import pytest

from bypasshub import awg, vpn
from bypasshub.core import Core
from bypasshub.paths import Paths

UUID = "dbd89350-e191-4307-9210-3246cbc7737e"
PBK = "oaS8SPvmhQniPEA840vcfWcr3C189UUDLzf3KfdHjlY"
REALITY = (f"vless://{UUID}@nl.example.com:443?encryption=none&security=reality&sni=www.microsoft.com"
           f"&fp=firefox&pbk={PBK}&sid=ab12&spx=%2F&type=tcp&flow=xtls-rprx-vision#NL%20%F0%9F%87%B3%F0%9F%87%B1")
WS = (f"vless://{UUID}@1.2.3.4:8443?security=tls&sni=cdn.example.com&allowInsecure=1&type=ws"
      f"&path=%2Fws%3Fed%3D2048&host=cdn.example.com#WS")

CONF = """[Interface]
Address = 10.8.1.2/32
DNS = $PRIMARY_DNS, $SECONDARY_DNS
PrivateKey = aGVsbG8taGVsbG8taGVsbG8taGVsbG8taGVsbG8tMTI=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 10
S2 = 20
H1 = 1
H2 = 2
H3 = 3
H4 = 4

[Peer]
PublicKey = d29ybGQtd29ybGQtd29ybGQtd29ybGQtd29ybGQtMTI=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 203.0.113.5:51820
PersistentKeepalive = 25
"""


def amnezia_key(conf: str = CONF, container: str = "amnezia-awg2", proto: str = "awg", **extra) -> str:
    """Ключ в формате AmneziaVPN: vpn:// + base64url(qCompress(JSON))."""
    last = json.dumps({"config": conf, "mtu": "1280", "hostName": "203.0.113.5", "port": 51820})
    data = {"containers": [{"container": container, proto: {"last_config": last, "port": "51820"}}],
            "defaultContainer": container, "description": "My VPS", "dns1": "9.9.9.9", "dns2": "",
            "hostName": "203.0.113.5"}
    data.update(extra)
    raw = json.dumps(data).encode()
    packed = len(raw).to_bytes(4, "big") + zlib.compress(raw)
    return "vpn://" + base64.urlsafe_b64encode(packed).decode().rstrip("=")


# ---------------------------------------------------------------- VLESS

def test_parse_reality():
    k = vpn.parse_vless(REALITY)
    assert (k.server, k.port, k.uuid) == ("nl.example.com", 443, UUID)
    assert k.security == "reality" and k.flow == "xtls-rprx-vision" and k.short_id == "ab12"
    assert k.name == "NL 🇳🇱"
    ob = vpn.outbound(k)
    assert ob["tls"]["server_name"] == "www.microsoft.com"
    assert ob["tls"]["utls"] == {"enabled": True, "fingerprint": "firefox"}
    assert ob["tls"]["reality"] == {"enabled": True, "public_key": PBK, "short_id": "ab12"}
    assert "transport" not in ob


def test_parse_ws_early_data():
    ob = vpn.outbound(vpn.parse_vless(WS))
    assert ob["transport"] == {"type": "ws", "path": "/ws", "max_early_data": 2048,
                               "early_data_header_name": "Sec-WebSocket-Protocol",
                               "headers": {"Host": "cdn.example.com"}}
    assert ob["tls"]["insecure"] is True and "utls" not in ob["tls"]


@pytest.mark.parametrize("uri, words", [
    ("vmess://abc", "VLESS"),
    (f"vless://{UUID}@h:443?type=xhttp&security=reality&pbk=x&sni=a", "XHTTP"),
    (f"vless://{UUID}@h:443?security=reality&sni=a", "pbk"),
    (f"vless://{UUID}@h:443?encryption=mlkem768x25519plus.native", "Шифрование"),
    (f"vless://{UUID}@h:99999", "порт"),
])
def test_parse_errors(uri, words):
    with pytest.raises(ValueError, match=words):
        vpn.parse_vless(uri)


def test_parse_keys_base64_subscription():
    body = base64.b64encode(f"{REALITY}\n{WS}\ntrojan://x@y:1#t\n".encode()).decode()
    keys, problems = vpn.parse_keys(body)
    assert [k.name for k in keys] == ["NL 🇳🇱", "WS"]
    assert any("других типов" in p for p in problems)


def test_build_config_tun_and_proxy(tmp_path):
    paths = {tag: tmp_path / f"{tag}.srs" for tag in vpn.RULE_SETS}
    for tag in ("rc-youtube", "rc-category-ru", "rc-direct-ip"):
        paths[tag].write_bytes(b"x")          # остальные базы «не скачались» — их правила пропускаются
    k = vpn.parse_vless(REALITY)
    cfg = vpn.build_config(vpn.outbound(k), "tun", True, 2080, paths)
    assert cfg["inbounds"][0]["type"] == "tun" and cfg["inbounds"][0]["strict_route"] is True
    rules = cfg["route"]["rules"]
    assert {"protocol": "dns", "action": "hijack-dns"} in rules
    assert [r["tag"] for r in cfg["route"]["rule_set"]] == ["rc-youtube", "rc-category-ru", "rc-direct-ip"]
    # YouTube — через VPN раньше, чем срабатывают российские IP (кэши YouTube стоят в России)
    order = [r.get("outbound") for r in rules if "rule_set" in r or "domain_suffix" in r]
    assert order == ["proxy", "direct", "direct", "direct"]
    assert {"domain_suffix": vpn.RU_SUFFIXES, "outbound": "direct"} in rules
    assert cfg["route"]["final"] == "proxy" and cfg["outbounds"][0]["tag"] == "proxy"
    assert cfg["outbounds"][0]["type"] == "vless"

    cfg = vpn.build_config(vpn.outbound(k), "proxy", False, 3000, paths)
    assert cfg["inbounds"][0] == {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 3000}
    assert not any(r.get("action") == "hijack-dns" for r in cfg["route"]["rules"])
    assert not any("domain_suffix" in r for r in cfg["route"]["rules"]) and "rule_set" not in cfg["route"]


def test_build_config_over_amneziawg():
    cfg = vpn.build_config(vpn.awg_outbound("BypassHub"), "tun", True, bypass=["203.0.113.5"])
    assert cfg["outbounds"][0] == {"type": "direct", "tag": "proxy", "bind_interface": "BypassHub"}
    # сервер AmneziaWG — мимо sing-box, иначе туннель завернётся сам в себя
    assert cfg["inbounds"][0]["route_exclude_address"] == ["203.0.113.5/32"]
    assert {"ip_cidr": ["203.0.113.5/32"], "outbound": "direct"} in cfg["route"]["rules"]


def test_pick_release_stays_in_supported_range():
    def rel(tag, pre=False):
        return {"tag_name": tag, "prerelease": pre, "assets": [{"name": f"sing-box-{tag[1:]}-windows-amd64.zip",
                                                               "browser_download_url": "u", "digest": "sha256:ab"}]}
    picked = vpn.pick_release([rel("v1.16.0"), rel("v1.15.0-beta.1", True), rel("v1.14.3"), rel("v1.13.9")])
    assert picked.version == "1.14.3"
    assert picked.assets["sing-box-1.14.3-windows-amd64.zip"].sha256 == "ab"


# ---------------------------------------------------------------- AmneziaWG

def test_amnezia_key():
    p = awg.conf_from_amnezia_key(amnezia_key())
    assert p.name == "My VPS" and p.endpoint == "203.0.113.5:51820"
    assert "DNS = 9.9.9.9\n" in p.conf and "$" not in p.conf   # подставлен DNS, лишняя запятая убрана
    assert "MTU = 1280" in p.conf
    assert "jc" in p.extras and "h4" in p.extras


def test_amnezia_key_errors():
    with pytest.raises(ValueError, match="повреждён"):
        awg.conf_from_amnezia_key("vpn://@@@")
    with pytest.raises(ValueError, match="openvpn"):
        awg.conf_from_amnezia_key(amnezia_key(container="amnezia-openvpn", proto="openvpn"))
    raw = json.dumps({"api_config": {}, "containers": []}).encode()
    premium = "vpn://" + base64.urlsafe_b64encode(len(raw).to_bytes(4, "big") + zlib.compress(raw)).decode()
    with pytest.raises(ValueError, match="Premium"):
        awg.conf_from_amnezia_key(premium)


def test_prepare_conf_lan_access():
    conf = awg.parse_conf(CONF).conf
    assert "AllowedIPs = 0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1" in awg.prepare_conf(conf, True)
    assert awg.prepare_conf(conf, False) == conf


def test_prepare_conf_split():
    out = awg.prepare_conf(awg.conf_from_amnezia_key(amnezia_key()).conf, split=True)
    assert "[Interface]\nTable = off\n" in out
    assert "DNS" not in out and "AllowedIPs = 0.0.0.0/0, ::/0" in out


def test_parse_conf_requires_peer():
    with pytest.raises(ValueError, match="Peer"):
        awg.parse_conf("[Interface]\nPrivateKey = x\n")


# ---------------------------------------------------------------- профили

def test_parse_input_mixed():
    text = f"{REALITY}\n{amnezia_key()}\nhttps://sub.example.com/abc\n"
    parsed = vpn.parse_input(text)
    assert [p.kind for p in parsed.profiles] == ["awg", "vless"]
    assert parsed.subscriptions == ["https://sub.example.com/abc"]
    assert vpn.parse_input(CONF).profiles[0].kind == "awg"
    assert vpn.parse_input("просто текст").problems


def test_core_profiles(tmp_path):
    core = Core(Paths(tmp_path))
    added, problems = core.vpn_add(f"{REALITY}\n{WS}")
    assert len(added) == 2 and not problems
    assert core.vpn_active().id == added[0].id
    again, _ = core.vpn_add(REALITY)
    assert again == []                      # повтор не добавляется
    core.vpn_select(added[1].id)
    core.vpn_rename(added[1].id, "Мой сервер")
    assert core.vpn_active().name == "Мой сервер"
    core.vpn_remove(added[1].id)
    assert core.vpn_active().id == added[0].id
    assert core.settings.get("vpn", "mode") == "tun"


def test_proxy_backup_restored_after_crash(tmp_path):
    m = vpn.VpnManager(tmp_path / "sb", tmp_path / "vpn.log")
    m.root.mkdir()
    m.proxy_backup.write_text(json.dumps({"enable": False, "server": "", "override": ""}))
    m.recover()
    assert not m.proxy_backup.exists()
