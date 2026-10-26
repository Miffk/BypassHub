import base64
import json
import zlib
from urllib.parse import quote

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


def test_awg_needs_wintun_next_to_exe(tmp_path):
    m = awg.AwgManager(tmp_path)
    m.exe.write_bytes(b"x")
    assert not m.is_installed()          # без wintun.dll служба туннеля не стартует — нужна переустановка
    (tmp_path / awg.WINTUN_DLL).write_bytes(b"x")
    assert m.is_installed()


def test_service_state_on_localized_windows():
    from bypasshub.winutil import parse_service_state
    ru = ("\nИмя_службы: AmneziaWGTunnel$BypassHub\n        Тип                : 10  WIN32_OWN_PROCESS\n"
          "        Состояние          : 4  RUNNING\n                                (STOPPABLE, NOT_PAUSABLE)\n"
          "        Код_выхода_Win32   : 0  (0x0)\n")
    en = ("SERVICE_NAME: zapret\n        TYPE               : 10  WIN32_OWN_PROCESS\n"
          "        STATE              : 1  STOPPED\n")
    assert parse_service_state(ru) == "RUNNING"
    assert parse_service_state(en) == "STOPPED"
    assert parse_service_state("мусор") == "UNKNOWN"


def test_server_group_config():
    servers = [vpn.outbound(vpn.parse_vless(REALITY), tag="vless:a"), vpn.outbound(vpn.parse_vless(WS), tag="vless:b")]
    group = vpn.server_group(servers, "vless:b")
    cfg = vpn.build_config(group, "tun", False, api=(9999, "s3cret"))
    tags = [ob["tag"] for ob in cfg["outbounds"]]
    assert tags == ["vless:a", "vless:b", "auto", "proxy", "direct"]
    auto, sel = cfg["outbounds"][2], cfg["outbounds"][3]
    assert auto["type"] == "urltest" and auto["outbounds"] == ["vless:a", "vless:b"]
    assert sel["type"] == "selector" and sel["default"] == "vless:b" and sel["outbounds"][0] == "auto"
    assert cfg["experimental"]["clash_api"] == {"external_controller": "127.0.0.1:9999", "secret": "s3cret"}
    assert vpn.server_group(servers, "нет такого")[-1]["default"] == "vless:a"


def test_core_auto_mode(tmp_path):
    core = Core(Paths(tmp_path))
    core.vpn_add(f"{REALITY}\n{WS}\n{amnezia_key()}")
    assert len(core.vpn_vless()) == 2
    core.vpn_select(vpn.AUTO_TAG)
    assert core.vpn_is_auto() and core.vpn_active() is None
    core.vpn_remove(core.vpn_vless()[0].id)       # остался один сервер VLESS — «авто» не имеет смысла
    assert not core.vpn_is_auto() and core.vpn_active().kind == "vless"


def test_subscription_update_interval():
    assert vpn.update_interval("12") == 12
    assert vpn.update_interval("0.1") == 1          # не чаще раза в час
    assert vpn.update_interval("100") == 24
    assert vpn.update_interval(None) == vpn.SUB_REFRESH_HOURS
    assert vpn.update_interval("abc") == vpn.SUB_REFRESH_HOURS


def test_subscription_auto_refresh(tmp_path, monkeypatch):
    """Подписка обновляется сама, когда пора: новые серверы появляются, выбранный сервер остаётся
    выбранным, даже если сервис сменил ему ключ; пустой ответ не стирает список."""
    sub = "https://sub.example.com/abc"
    other_uuid = "11111111-2222-3333-4444-555555555555"
    answers = {"keys": [REALITY]}
    calls = []

    def fake_fetch(url, timeout=20, hwid=""):
        assert hwid  # код устройства уходит в каждую подписку
        calls.append(url)
        keys = [vpn.parse_vless(k) for k in answers["keys"]]
        return keys, [], 6.0

    monkeypatch.setattr(vpn, "fetch_subscription_info", fake_fetch)
    core = Core(Paths(tmp_path))
    added, _ = core.vpn_add(sub)
    assert [p.name for p in added] == ["NL 🇳🇱"] and core.vpn_active().sub == sub
    assert not core._sub_due(sub)
    core.vpn_refresh_subscriptions(only_due=True)
    assert len(calls) == 1                           # ещё рано — не скачивали

    # сервис добавил сервер и сменил ключ у выбранного
    answers["keys"] = [REALITY.replace(UUID, other_uuid), WS]
    meta = core.settings.get("vpn", "subs_meta")
    meta[sub]["checked"] -= 7 * 3600
    assert core._sub_due(sub)
    total, problems = core.vpn_refresh_subscriptions(only_due=True)
    assert total == 2 and not problems
    assert [p.name for p in core.vpn_profiles()] == ["NL 🇳🇱", "WS"]
    assert core.vpn_active().name == "NL 🇳🇱" and other_uuid in core.vpn_active().data

    answers["keys"] = []
    total, problems = core.vpn_refresh_subscriptions()
    assert len(core.vpn_profiles()) == 2 and problems  # сбой сервиса — серверы на месте


def test_exclude_apps_rule():
    assert vpn.clean_exe_names([r'"C:\Games\Dota 2\dota2.exe"', "CS2", "cs2.exe", "", "league of legends.exe"]) \
        == ["dota2.exe", "cs2.exe", "league of legends.exe"]
    ob = vpn.outbound(vpn.parse_vless(REALITY))
    cfg = vpn.build_config(ob, "tun", False, exclude_apps=["dota2.exe"])
    rules = cfg["route"]["rules"]
    assert rules[0] == {"action": "sniff"} and rules[1]["action"] == "hijack-dns"
    assert rules[2] == {"process_path_regex": ["(?i)(^|[\\\\/])dota2\\.exe$"], "outbound": "direct"}
    proxy_cfg = vpn.build_config(ob, "proxy", False, exclude_apps=["dota2.exe"])
    assert not any("process_path_regex" in r for r in proxy_cfg["route"]["rules"])
    assert not any("process_path_regex" in r for r in vpn.build_config(ob, "tun", False)["route"]["rules"])


def test_xray_json_subscription():
    """Подписка в формате Xray JSON (как её получает Happ) — серверы VLESS из исходящих."""
    cfg = [
        {"remarks": "🇩🇪 Germany", "outbounds": [
            {"protocol": "vless", "tag": "proxy", "settings": {"vnext": [{"address": "de.example.com", "port": 443,
             "users": [{"id": UUID, "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                 "serverName": "www.microsoft.com", "publicKey": PBK, "shortId": "ab12", "fingerprint": "chrome"}}},
            {"protocol": "freedom", "tag": "direct"}]},
        {"remarks": "🇳🇱 WS", "outbounds": [
            {"protocol": "vless", "settings": {"vnext": [{"address": "1.2.3.4", "port": 8443,
             "users": [{"id": UUID}]}]},
             "streamSettings": {"network": "ws", "security": "tls", "tlsSettings": {"serverName": "cdn.example.com"},
                                "wsSettings": {"path": "/ws", "headers": {"Host": "cdn.example.com"}}}}]},
        {"remarks": "trojan", "outbounds": [{"protocol": "trojan", "settings": {}}]},
    ]
    keys, problems = vpn.parse_keys(json.dumps(cfg, ensure_ascii=False))
    assert [k.name for k in keys] == ["🇩🇪 Germany", "🇳🇱 WS"]
    assert problems == ["пропущено ключей других типов (не VLESS и не Hysteria2) (trojan): 1"]
    de, ws = keys
    assert (de.server, de.security, de.public_key, de.sni, de.flow) == \
        ("de.example.com", "reality", PBK, "www.microsoft.com", "xtls-rprx-vision")
    assert (ws.transport, ws.path, ws.host, ws.sni) == ("ws", "/ws", "cdn.example.com", "cdn.example.com")


def test_subscription_asks_as_known_clients(monkeypatch):
    """Сервис отдаёт незнакомой программе один сервер, а Happ — все: берётся самый полный ответ."""
    from bypasshub import netfetch
    seen = []

    def fake_fetch(url, headers, timeout=20, limit=0):
        ua = headers["User-Agent"]
        seen.append(ua)
        body = REALITY if not ua.startswith("Happ") else f"{REALITY}\n{WS}"
        return netfetch.Response(body.encode(), {"profile-update-interval": "3"}, url)

    monkeypatch.setattr(netfetch, "fetch", fake_fetch)
    keys, problems, hours = vpn.fetch_subscription_info("https://sub.example.com/x")
    assert len(keys) == 2 and hours == 3 and seen == vpn.SUB_USER_AGENTS


def test_subscription_sends_device_id_and_shows_notice(monkeypatch):
    """Панель с лимитом устройств: без кода устройства — заглушка с сообщением вместо серверов."""
    from bypasshub import netfetch
    stub = f"vless://{UUID}@0.0.0.0:1?type=tcp&security=none#" + quote("Приложение не поддерживается")

    def fake_fetch(url, headers, timeout=20, limit=0):
        body = f"{REALITY}\n{WS}" if headers.get("x-hwid") == "abc123" else stub
        return netfetch.Response(body.encode(), {}, url)

    monkeypatch.setattr(netfetch, "fetch", fake_fetch)
    keys, problems, _ = vpn.fetch_subscription_info("https://sub.example.com/x", hwid="abc123")
    assert len(keys) == 2 and not problems
    keys, problems, _ = vpn.fetch_subscription_info("https://sub.example.com/x")
    assert keys == [] and problems == ["сервис подписки сообщает: «Приложение не поддерживается»"]


def test_fetch_falls_back_to_doh(monkeypatch):
    """Системный DNS не нашёл адрес — запрос идёт через DNS по HTTPS."""
    import socket
    from urllib.error import URLError
    from bypasshub import netfetch

    def no_dns(*a, **kw):
        raise URLError(socket.gaierror(11001, "getaddrinfo failed"))

    calls = []
    monkeypatch.setattr(netfetch, "urlopen", no_dns)
    monkeypatch.setattr(netfetch, "_fetch_pinned",
                        lambda url, headers, timeout, limit: calls.append(url) or netfetch.Response(b"ok", {}, url))
    assert netfetch.fetch("https://sub.example.com/x", {}).body == b"ok" and calls == ["https://sub.example.com/x"]

    def refused(*a, **kw):
        raise URLError(ConnectionRefusedError())
    monkeypatch.setattr(netfetch, "urlopen", refused)
    with pytest.raises(URLError):
        netfetch.fetch("https://sub.example.com/x", {})   # не DNS — запасной путь не нужен


def test_awg_waits_for_slow_adapter(tmp_path, monkeypatch):
    """Служба туннеля долго создаёт адаптер (START_PENDING больше 10 с) — это ещё не ошибка."""
    import subprocess
    from bypasshub import winutil
    m = awg.AwgManager(tmp_path)
    for name in (awg.EXE_NAME, awg.WINTUN_DLL):
        (tmp_path / name).write_bytes(b"x")
    states = iter([None] + ["START_PENDING"] * 200 + ["RUNNING"])
    monkeypatch.setattr(winutil, "service_state", lambda name: next(states))
    monkeypatch.setattr(winutil, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(awg.time, "sleep", lambda s: None)
    monkeypatch.setattr(awg.AwgManager, "_wait_adapter_gone", staticmethod(lambda timeout=10: None))
    m.start(awg.parse_conf(CONF))  # без исключения: дождались RUNNING


def test_service_pid_from_sc_queryex():
    from bypasshub import winutil
    out = ("SERVICE_NAME: AmneziaWGTunnel$BypassHub\n        TYPE               : 10  WIN32_OWN_PROCESS\n"
           "        STATE              : 2  START_PENDING\n        PID                : 4321\n")
    assert winutil.parse_service_pid(out) == 4321 and winutil.parse_service_pid("") == 0


def test_github_requests_fall_back_to_doh(monkeypatch):
    """Системный DNS не находит GitHub — проверка обновлений идёт через DNS по HTTPS."""
    import socket
    from urllib.error import URLError
    from bypasshub import github, netfetch

    def no_dns(*a, **kw):
        raise URLError(socket.gaierror(11001, "getaddrinfo failed"))

    class Fake:
        headers = {}

        def __init__(self, url):
            self.url = url

        def read(self, n=-1):
            return json.dumps({"tag_name": "v1.2.3", "assets": []}).encode()

        def geturl(self):
            return self.url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(netfetch, "urlopen", no_dns)
    monkeypatch.setattr(netfetch, "_open_pinned", lambda url, headers, timeout: Fake(url))
    assert github.latest_release("a/b").tag == "v1.2.3"


def test_xray_json_simplified_vless_and_description():
    """Новая запись Xray (адрес, порт и id прямо в settings) — тоже сервер; в журнал — формат ответа."""
    cfg = [{"remarks": "FI", "outbounds": [
        {"protocol": "vless", "settings": {"address": "fi.example.com", "port": 443, "id": UUID, "encryption": "none"},
         "streamSettings": {"network": "grpc", "security": "tls", "grpcSettings": {"serviceName": "g"}}},
        {"protocol": "freedom"}]},
        {"remarks": "bad", "outbounds": [{"protocol": "vless", "settings": {"port": 443}}]}]
    text = json.dumps(cfg)
    keys, problems = vpn.parse_keys(text)
    assert [(k.name, k.server, k.transport) for k in keys] == [("FI", "fi.example.com", "grpc")]
    assert problems == ["не удалось разобрать серверов VLESS в формате Xray JSON: 1"]
    assert vpn.describe_subscription(text) == "Xray JSON, конфигов 2, исходящие: vless×2, freedom×1"
    links = base64.b64encode(f"{REALITY}\nhysteria2://x@h:1#a\nhysteria2://y@h:2#b".encode()).decode()
    assert vpn.describe_subscription(links) == "base64, схемы: hysteria2×2, vless×1"


HY2 = "hysteria2://p%40ss@de.example.com:443/?sni=de.example.com&obfs=salamander&obfs-password=salt&mport=20000-50000#DE%20Hysteria"


def test_hysteria2_key_and_outbound():
    k = vpn.parse_vless(HY2)
    assert (k.protocol, k.server, k.port, k.uuid, k.obfs_password, k.server_ports, k.name) == \
        ("hysteria2", "de.example.com", 443, "p@ss", "salt", "20000-50000", "DE Hysteria")
    ob = vpn.outbound(k, "x")
    assert ob == {"type": "hysteria2", "tag": "x", "server": "de.example.com", "server_port": 443,
                  "password": "p@ss", "tls": {"enabled": True, "server_name": "de.example.com"},
                  "obfs": {"type": "salamander", "password": "salt"}, "server_ports": ["20000:50000"]}
    # порты списком в адресе, короткая схема hy2://
    k2 = vpn.parse_vless("hy2://secret@1.2.3.4:443,5000-6000?insecure=1#x")
    assert (k2.port, k2.server_ports, k2.insecure, k2.label) == (443, "5000-6000", True, "Hysteria2")
    keys, problems = vpn.parse_keys(f"{REALITY}\n{HY2}\ntrojan://x@h:1#t")
    assert [k.protocol for k in keys] == ["vless", "hysteria2"]
    assert problems == ["пропущено ключей других типов (не VLESS и не Hysteria2): 1"]


def test_xray_json_hysteria():
    """Hysteria в Xray JSON (так их отдаёт Remnawave для Happ) — тоже серверы."""
    cfg = [
        {"remarks": "🇩🇪 Molniya | Hysteria", "outbounds": [
            {"protocol": "hysteria", "tag": "proxy", "settings": {"version": 2, "address": "de.example.com", "port": 443},
             "streamSettings": {"network": "hysteria", "security": "tls",
                                "tlsSettings": {"serverName": "de.example.com", "alpn": ["h3"]},
                                "hysteriaSettings": {"version": 2, "auth": "pw", "udphop": {"port": "20000-30000",
                                                                                           "interval": 30}},
                                "finalmask": {"udp": [{"type": "salamander", "settings": {"password": "salt"}}]}}},
            {"protocol": "freedom", "tag": "direct"}]},
        {"remarks": "broken", "outbounds": [{"protocol": "hysteria", "settings": {"version": 2}}]},
    ]
    keys, problems = vpn.parse_keys(json.dumps(cfg, ensure_ascii=False))
    assert len(keys) == 1
    k = keys[0]
    assert (k.protocol, k.name, k.server, k.uuid, k.sni, k.alpn, k.obfs_password, k.server_ports, k.hop_interval) == \
        ("hysteria2", "🇩🇪 Molniya | Hysteria", "de.example.com", "pw", "de.example.com", ["h3"], "salt",
         "20000-30000", 30)
    assert problems == ["не удалось разобрать серверов Hysteria в формате Xray JSON: 1"]


def test_hysteria_in_server_group_config():
    servers = [vpn.outbound(vpn.parse_vless(REALITY), tag="a"), vpn.outbound(vpn.parse_vless(HY2), tag="b")]
    cfg = vpn.build_config(vpn.server_group(servers, vpn.AUTO_TAG), mode="tun")
    types = {ob["tag"]: ob["type"] for ob in cfg["outbounds"]}
    assert types["a"] == "vless" and types["b"] == "hysteria2"


def test_ping_through_servers_when_vpn_off(tmp_path, monkeypatch):
    """VPN выключен: задержка меряется через сами серверы (и Hysteria2), а не TCP-подключением."""
    core = Core(Paths(tmp_path))
    core.vpn_add(f"{REALITY}\n{HY2}")
    ids = [p.id for p in core.vpn_vless()]
    seen = {}

    def probe(servers, timeout_ms=5000):
        seen["types"] = [s["type"] for s in servers]
        return {ids[1]: 42}

    monkeypatch.setattr(core.singbox, "probe_delays", probe)
    monkeypatch.setattr(vpn, "ping", lambda key, timeout=4.0: pytest.fail("TCP-пинг не нужен"))
    result, real = core.vpn_ping()
    assert real and seen["types"] == ["vless", "hysteria2"] and result == {ids[0]: None, ids[1]: 42.0}

    # движок не скачан — TCP-пинг для VLESS, Hysteria2 без замера («—»)
    monkeypatch.setattr(core.singbox, "probe_delays", lambda servers, timeout_ms=5000: None)
    monkeypatch.setattr(vpn, "ping", lambda key, timeout=4.0: 12.0)
    result, real = core.vpn_ping()
    assert not real and result == {ids[0]: 12.0}


def test_probe_delays_runs_separate_singbox(tmp_path, monkeypatch):
    from bypasshub import winutil
    m = vpn.VpnManager(tmp_path, tmp_path / "vpn.log")
    m.exe.write_bytes(b"x")
    started, killed, calls = [], [], []

    class Proc:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, cwd=None, stdout=None, detached=False):
        cfg = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
        started.append(cfg)
        return Proc()

    def fake_api(path, method="GET", body=None, timeout=3.0, api=None):
        calls.append(path)
        assert api is not None and m._probe_pid == 4242
        return {"a": 30, "b": 0} if path.startswith("/group/") else {"version": "x"}

    monkeypatch.setattr(winutil, "popen_hidden", fake_popen)
    monkeypatch.setattr(winutil, "kill_pid_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(m, "api_request", fake_api)
    servers = [vpn.outbound(vpn.parse_vless(REALITY), "a"), vpn.outbound(vpn.parse_vless(HY2), "b")]
    assert m.probe_delays(servers) == {"a": 30}
    cfg = started[0]
    assert "inbounds" not in cfg and cfg["experimental"]["clash_api"]["external_controller"].startswith("127.0.0.1:")
    assert killed == [4242] and m._probe_pid == 0 and not (tmp_path / "probe.json").exists()
