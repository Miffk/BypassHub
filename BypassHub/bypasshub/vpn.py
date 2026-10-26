"""Свой VPN по ключу VLESS. Движок — sing-box (SagerNet).

Пользователь вставляет ключ vless://… или ссылку на подписку своего VPN-сервиса.
BypassHub скачивает официальный sing-box из релизов, сам собирает для него
config.json и запускает в одном из режимов:

* «Весь трафик» — виртуальный сетевой адаптер (TUN): через VPN идут все
  программы, включая игры и Discord;
* «Системный прокси» — локальный прокси, который прописывается в настройках
  Windows: через VPN идут браузеры и программы, которые эти настройки учитывают.

Российские сайты по желанию идут напрямую, мимо VPN.
"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from . import github, netfetch, winutil
from .log import log

REPO = "SagerNet/sing-box"
EXE_NAME = "sing-box.exe"
# Конфиг пишется под формат sing-box 1.12+ (новый формат DNS-серверов); в 1.16
# удаляются устаревшие поля, поэтому обновления берутся только из этого диапазона.
MIN_VERSION = (1, 12)
MAX_VERSION = (1, 16)  # не включая
PINNED_VERSION = "1.14.3"  # если GitHub API недоступен

INTERFACE_NAME = "BypassHub VPN"
AUTO_TAG = "auto"                     # группа «лучший сервер»
URLTEST_URL = "https://www.gstatic.com/generate_204"
TUN_ADDRESS = "172.19.0.1/30"
DEFAULT_PROXY_PORT = 2080

MODES = {"tun": "Весь трафик", "proxy": "Системный прокси"}

# Раздельное туннелирование — базы roscomvpn (hydraponique/roscomvpn-geosite и -geoip):
# российские сервисы на «нерусских» доменах (VK, Яндекс, банки…) и российские IP-адреса.
_RC = "https://raw.githubusercontent.com/hydraponique/roscomvpn-{repo}/release/sing-box/{name}.srs"
RULE_SETS = {
    # всегда через VPN — даже если сервер оказался на российском IP (кэши YouTube, Google)
    "rc-youtube": _RC.format(repo="geosite", name="youtube"),
    "rc-telegram": _RC.format(repo="geosite", name="telegram"),
    "rc-github": _RC.format(repo="geosite", name="github"),
    "rc-google-play": _RC.format(repo="geosite", name="google-play"),
    # напрямую
    "rc-category-ru": _RC.format(repo="geosite", name="category-ru"),
    "rc-whitelist": _RC.format(repo="geosite", name="whitelist"),
    "rc-direct-ip": _RC.format(repo="geoip", name="direct"),
}
PROXY_SETS = ["rc-youtube", "rc-telegram", "rc-github", "rc-google-play"]
DIRECT_SITE_SETS = ["rc-category-ru", "rc-whitelist"]
DIRECT_IP_SETS = ["rc-direct-ip"]
RULES_MAX_AGE = 3 * 24 * 3600  # базы обновляются раз в несколько дней
RU_SUFFIXES = ["ru", "su", "xn--p1ai"]  # .ru, .su, .рф

UTLS_FINGERPRINTS = {"chrome", "firefox", "edge", "safari", "360", "qq", "ios", "android", "random",
                     "randomized"}
_TRANSPORTS = {"tcp", "raw", "ws", "grpc", "http", "h2", "httpupgrade"}


# ====================================================================== ключи VLESS

@dataclass
class VlessKey:
    uri: str
    name: str
    server: str
    port: int
    uuid: str
    security: str = "none"         # none | tls | reality
    transport: str = "tcp"         # tcp | ws | grpc | http | httpupgrade
    flow: str = ""
    sni: str = ""
    fingerprint: str = ""
    public_key: str = ""
    short_id: str = ""
    alpn: List[str] = field(default_factory=list)
    insecure: bool = False
    path: str = ""
    host: str = ""
    service_name: str = ""

    @property
    def summary(self) -> str:
        """Коротко для списка: «nl.example.com:443 · Reality · TCP»."""
        sec = {"reality": "Reality", "tls": "TLS", "none": "без шифрования TLS"}.get(self.security, self.security)
        tr = {"tcp": "TCP", "ws": "WebSocket", "grpc": "gRPC", "http": "HTTP/2",
              "httpupgrade": "HTTPUpgrade"}.get(self.transport, self.transport)
        return f"{self.server}:{self.port} · {sec} · {tr}"


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def parse_vless(uri: str) -> VlessKey:
    """Разбор ключа vless://uuid@host:port?параметры#имя (формат Xray / v2rayN)."""
    uri = uri.strip()
    if not uri.lower().startswith("vless://"):
        raise ValueError("Это не ключ VLESS — он должен начинаться с vless://")
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError:
        raise ValueError("В ключе неверный порт сервера") from None
    uuid = unquote(parts.username or "").strip()
    server = (parts.hostname or "").strip()
    if not uuid or not server or not port:
        raise ValueError("В ключе нет адреса сервера, порта или идентификатора (UUID)")
    q = {k: v.strip() for k, v in parse_qsl(parts.query, keep_blank_values=True)}

    encryption = q.get("encryption", "none").lower()
    if encryption not in ("", "none"):
        raise ValueError(f"Шифрование VLESS «{encryption}» пока не поддерживается движком sing-box")

    transport = (q.get("type") or "tcp").lower()
    if transport in ("xhttp", "splithttp"):
        raise ValueError("Ключи с транспортом XHTTP пока не поддерживаются движком sing-box. "
                         "Попросите у своего VPN-сервиса ключ с TCP (Reality), WebSocket или gRPC.")
    if transport not in _TRANSPORTS:
        raise ValueError(f"Транспорт «{transport}» не поддерживается")
    transport = {"raw": "tcp", "h2": "http"}.get(transport, transport)
    if transport == "tcp" and q.get("headerType", "none").lower() not in ("", "none"):
        raise ValueError("Маскировка TCP под HTTP (headerType=http) не поддерживается движком sing-box")

    security = (q.get("security") or "none").lower()
    if security not in ("none", "tls", "reality"):
        raise ValueError(f"Тип защиты «{security}» не поддерживается")

    flow = q.get("flow", "")
    if flow.startswith("xtls-rprx-vision"):
        flow = "xtls-rprx-vision"
    elif flow:
        raise ValueError(f"Режим flow «{flow}» не поддерживается")

    key = VlessKey(
        uri=uri, name=unquote(parts.fragment).strip() or f"{server}:{port}", server=server, port=port,
        uuid=uuid, security=security, transport=transport, flow=flow,
        sni=q.get("sni") or q.get("peer") or "", fingerprint=q.get("fp", "").lower(),
        public_key=q.get("pbk", ""), short_id=q.get("sid", ""),
        alpn=[a.strip() for a in q.get("alpn", "").split(",") if a.strip()],
        insecure=q.get("allowInsecure", q.get("insecure", "")).lower() in ("1", "true"),
        path=q.get("path", ""), host=q.get("host", ""), service_name=q.get("serviceName", ""),
    )
    if security == "reality":
        if not key.public_key:
            raise ValueError("В ключе Reality нет открытого ключа сервера (pbk)")
        if not key.sni:
            raise ValueError("В ключе Reality нет имени сайта для маскировки (sni)")
    return key


def _decode_base64(text: str) -> Optional[str]:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None
    compact += "=" * (-len(compact) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(compact).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
    return None


_PROXY_PROTOCOLS = {"vmess", "trojan", "shadowsocks", "hysteria", "hysteria2", "tuic", "wireguard", "socks", "http"}


def _xray_json_links(text: str, skipped: Optional[List[str]] = None) -> Optional[List[str]]:
    """Подписка в формате Xray JSON (так её отдают Happ и Streisand): список готовых конфигов Xray.
    Из каждого берётся исходящее VLESS и пересобирается в ссылку vless://. None — это не JSON.
    В skipped попадают протоколы конфигов без VLESS (их серверы пропущены)."""
    stripped = text.strip()
    if not stripped.startswith(("[", "{")):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    configs = data if isinstance(data, list) else [data]
    links: List[str] = []
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        name = str(cfg.get("remarks") or "")
        before = len(links)
        others = [str(ob.get("protocol")) for ob in cfg.get("outbounds") or []
                  if isinstance(ob, dict) and ob.get("protocol") in _PROXY_PROTOCOLS]
        for ob in cfg.get("outbounds") or []:
            if not isinstance(ob, dict) or ob.get("protocol") != "vless":
                continue
            try:
                vnext = ob["settings"]["vnext"][0]
                user = vnext["users"][0]
                host, port, uid = str(vnext["address"]), int(vnext["port"]), str(user["id"])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            ss = ob.get("streamSettings") or {}
            net = ss.get("network") or "tcp"
            sec = ss.get("security") or "none"
            q: Dict[str, str] = {"encryption": str(user.get("encryption") or "none"), "type": net, "security": sec}
            if user.get("flow"):
                q["flow"] = str(user["flow"])
            if sec == "reality":
                r = ss.get("realitySettings") or {}
                q.update(sni=r.get("serverName", ""), pbk=r.get("publicKey", ""), sid=r.get("shortId", ""),
                         fp=r.get("fingerprint", ""), spx=r.get("spiderX", ""))
            elif sec == "tls":
                t = ss.get("tlsSettings") or {}
                q.update(sni=t.get("serverName", ""), fp=t.get("fingerprint", ""),
                         alpn=",".join(t.get("alpn") or []))
                if t.get("allowInsecure"):
                    q["allowInsecure"] = "1"
            if net == "ws":
                w = ss.get("wsSettings") or {}
                q.update(path=w.get("path", ""), host=(w.get("headers") or {}).get("Host", "") or w.get("host", ""))
            elif net == "grpc":
                q["serviceName"] = (ss.get("grpcSettings") or {}).get("serviceName", "")
            elif net == "httpupgrade":
                h = ss.get("httpupgradeSettings") or {}
                q.update(path=h.get("path", ""), host=h.get("host", ""))
            elif net == "xhttp":
                x = ss.get("xhttpSettings") or {}
                q.update(path=x.get("path", ""), host=x.get("host", ""))
            query = urlencode({k: v for k, v in q.items() if v not in ("", None)})
            hostpart = f"[{host}]" if ":" in host else host
            links.append(f"vless://{quote(uid)}@{hostpart}:{port}?{query}#{quote(name or host)}")
            break  # в конфиге одно исходящее «proxy», остальные — direct/block
        if len(links) == before and others and skipped is not None:
            skipped.append(others[0])
    return links


def parse_keys(text: str) -> Tuple[List[VlessKey], List[str]]:
    """Ключи из вставленного текста или ответа подписки (ссылки, base64 или Xray JSON).
    Возвращает (ключи VLESS, сообщения о пропущенных строках)."""
    json_skipped: List[str] = []
    links = _xray_json_links(text, json_skipped)
    if links is not None:
        text = "\n".join(links)
    if "://" not in text:
        decoded = _decode_base64(text)
        if decoded:
            text = decoded
    keys: List[VlessKey] = []
    problems: List[str] = []
    other = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or "://" not in line:
            continue
        if not line.lower().startswith("vless://"):
            other += 1
            continue
        try:
            keys.append(parse_vless(line))
        except ValueError as exc:
            name = unquote(line.rsplit("#", 1)[1]) if "#" in line else line[:40] + "…"
            problems.append(f"{name}: {exc}")
    other += len(json_skipped)
    if other:
        kinds = f" ({', '.join(sorted(set(json_skipped)))})" if json_skipped else ""
        problems.append(f"пропущено ключей других типов (не VLESS){kinds}: {other}")
    return keys, problems


# Как часто обновлять подписку, если она сама не сказала (заголовок profile-update-interval, в часах —
# его понимают Happ, v2rayN и др.): VPN-сервисы добавляют и меняют серверы, список должен успевать за ними.
SUB_REFRESH_HOURS = 6.0


def update_interval(header: Optional[str]) -> float:
    try:
        hours = float((header or "").strip())
    except ValueError:
        return SUB_REFRESH_HOURS
    return min(max(hours, 1.0), 24.0) if hours > 0 else SUB_REFRESH_HOURS


# Сервисы подписок (Remnawave, Marzban и др.) смотрят, какая программа спрашивает, и разным клиентам
# отдают разное: полный список часто получают только известные (Happ, v2rayN), а незнакомой программе
# достаётся урезанный. Поэтому подписка запрашивается от имени каждого и берётся самый полный ответ.
SUB_USER_AGENTS = [github.USER_AGENT, "v2rayN/7.15.4", "Happ/3.9.0"]

# Адреса, на которых сервис присылает не сервер, а сообщение в названии «ключа» (Remnawave так отвечает,
# когда достигнут лимит устройств, программа не прислала код устройства, подписка истекла и т.п.)
_PLACEHOLDER_HOSTS = {"0.0.0.0", "127.0.0.1", "::", "::1", "localhost"}


def is_placeholder(key: VlessKey) -> bool:
    return key.server.strip("[]").lower() in _PLACEHOLDER_HOSTS or key.port <= 1


def device_headers(hwid: str) -> Dict[str, str]:
    """Код устройства, как его присылают Happ и v2RayTun. Панели с лимитом устройств (Remnawave и др.)
    без него отдают вместо серверов заглушку. Код случайный и хранится в настройках, имя ПК не уходит."""
    if not hwid:
        return {}
    import platform
    return {"x-hwid": hwid, "x-device-os": "Windows", "x-ver-os": platform.release() or "10",
            "x-device-model": "PC"}


def fetch_subscription_info(url: str, timeout: float = 20,
                            hwid: str = "") -> Tuple[List[VlessKey], List[str], float]:
    """Ключи подписки, предупреждения и через сколько часов её обновить."""
    best: Optional[Tuple[List[VlessKey], List[str], float]] = None
    notices: List[str] = []  # сообщения сервиса вместо серверов
    host = urlsplit(url).hostname or "?"
    for ua in SUB_USER_AGENTS:
        headers = {"User-Agent": ua, "Accept": "*/*", "Cache-Control": "no-cache"}
        headers.update(device_headers(hwid))
        try:
            resp = netfetch.fetch(url, headers, timeout)
        except HTTPError as exc:
            log.info("Подписка %s от имени %s: HTTP %s", host, ua, exc.code)
            if ua == SUB_USER_AGENTS[-1] and best is None:
                raise
            continue  # этому клиенту сервис не отвечает — пробуем от имени следующего
        except Exception:
            if best is None:
                raise  # сервис недоступен вовсе — другие попытки тоже не помогут
            break
        found, problems = parse_keys(resp.body.decode("utf-8", errors="replace"))
        keys = [k for k in found if not is_placeholder(k)]
        stubs = [k.name for k in found if is_placeholder(k)]
        notices += [n for n in stubs if n not in notices]
        log.info("Подписка %s от имени %s: серверов %d%s", host, ua, len(keys),
                 f", сообщения сервиса: {stubs}" if stubs else "")
        hours = update_interval(resp.headers.get("profile-update-interval"))
        if best is None or len(keys) > len(best[0]):
            best = (keys, problems, hours)
    assert best is not None
    keys, problems, hours = best
    if notices and len(keys) <= 1:
        problems = problems + [f"сервис подписки сообщает: «{n}»" for n in notices]
    return keys, problems, hours


def fetch_subscription(url: str, timeout: float = 20, hwid: str = "") -> Tuple[List[VlessKey], List[str]]:
    keys, problems, _ = fetch_subscription_info(url, timeout, hwid)
    return keys, problems


# ====================================================================== профили (VLESS и AmneziaWG)

@dataclass
class Profile:
    """Сервер в списке VPN. kind: vless — data хранит ключ vless://…; awg — текст .conf."""
    id: str
    kind: str
    name: str
    data: str
    sub: str = ""      # адрес подписки, из которой взят ключ

    @property
    def summary(self) -> str:
        try:
            if self.kind == "awg":
                from .awg import parse_conf
                return parse_conf(self.data, self.name).summary
            return parse_vless(self.data).summary
        except ValueError as exc:
            return f"ошибка: {exc}"

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "kind": self.kind, "name": self.name, "data": self.data, "sub": self.sub}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Optional["Profile"]:
        try:
            return cls(str(d["id"]), str(d["kind"]), str(d.get("name") or ""), str(d["data"]),
                       str(d.get("sub") or ""))
        except (KeyError, TypeError):
            return None


def profile_id(kind: str, data: str) -> str:
    import hashlib
    return f"{kind}:{hashlib.sha1(data.strip().encode('utf-8')).hexdigest()[:12]}"


def vless_profile(key: VlessKey, sub: str = "") -> Profile:
    return Profile(profile_id("vless", key.uri), "vless", key.name, key.uri, sub)


def awg_profile(p: Any) -> Profile:
    return Profile(profile_id("awg", p.conf), "awg", p.name, p.conf)


@dataclass
class ParsedInput:
    profiles: List[Profile] = field(default_factory=list)
    subscriptions: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def parse_input(text: str) -> ParsedInput:
    """Что угодно из буфера обмена: ключи vless://, ключ AmneziaVPN vpn://, текст .conf
    AmneziaWG/WireGuard или ссылка на подписку https://…"""
    from . import awg
    out = ParsedInput()
    text = text.strip()
    if not text:
        return out
    if awg.looks_like_conf(text):
        try:
            out.profiles.append(awg_profile(awg.parse_conf(text)))
        except ValueError as exc:
            out.problems.append(str(exc))
        return out
    rest = []
    for line in text.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("vpn://"):
            try:
                out.profiles.append(awg_profile(awg.conf_from_amnezia_key(line)))
            except ValueError as exc:
                out.problems.append(str(exc))
        elif low.startswith(("http://", "https://")):
            out.subscriptions.append(line)
        else:
            rest.append(line)
    if any(line for line in rest):
        keys, problems = parse_keys("\n".join(rest))
        out.profiles += [vless_profile(k) for k in keys]
        out.problems += problems
        if not keys and not problems and not out.profiles and not out.subscriptions:
            out.problems.append("Не нашёл ключей. Подходят vless://…, vpn://… (AmneziaVPN), "
                                "конфиг AmneziaWG (.conf) или ссылка на подписку.")
    return out


# ====================================================================== конфиг sing-box

def outbound(key: VlessKey, tag: str = "proxy") -> Dict[str, Any]:
    ob: Dict[str, Any] = {"type": "vless", "tag": tag, "server": key.server, "server_port": key.port,
                          "uuid": key.uuid, "packet_encoding": "xudp"}
    if key.flow:
        ob["flow"] = key.flow
    if key.security in ("tls", "reality"):
        tls: Dict[str, Any] = {"enabled": True}
        server_name = key.sni or ("" if _is_ip(key.server) else key.server)
        if server_name:
            tls["server_name"] = server_name
        if key.alpn:
            tls["alpn"] = key.alpn
        if key.insecure:
            tls["insecure"] = True
        fp = key.fingerprint if key.fingerprint in UTLS_FINGERPRINTS else ""
        if key.security == "reality":
            tls["utls"] = {"enabled": True, "fingerprint": fp or "chrome"}
            tls["reality"] = {"enabled": True, "public_key": key.public_key, "short_id": key.short_id}
        elif fp:
            tls["utls"] = {"enabled": True, "fingerprint": fp}
        ob["tls"] = tls
    if key.transport == "ws":
        tr: Dict[str, Any] = {"type": "ws", "path": key.path or "/"}
        m = re.search(r"[?&]ed=(\d+)", tr["path"])
        if m:  # ранние данные: путь вида /ws?ed=2048
            tr["path"] = re.sub(r"[?&]ed=\d+", "", tr["path"]) or "/"
            tr["max_early_data"] = int(m.group(1))
            tr["early_data_header_name"] = "Sec-WebSocket-Protocol"
        if key.host:
            tr["headers"] = {"Host": key.host}
        ob["transport"] = tr
    elif key.transport == "grpc":
        ob["transport"] = {"type": "grpc", "service_name": key.service_name}
    elif key.transport == "http":
        tr = {"type": "http", "path": key.path or "/"}
        hosts = [h.strip() for h in key.host.split(",") if h.strip()]
        if hosts:
            tr["host"] = hosts
        ob["transport"] = tr
    elif key.transport == "httpupgrade":
        tr = {"type": "httpupgrade", "path": key.path or "/"}
        if key.host:
            tr["host"] = key.host
        ob["transport"] = tr
    return ob


def awg_outbound(interface: str, tag: str = "proxy") -> Dict[str, Any]:
    """«Через VPN» = в туннель AmneziaWG: исходящие соединения привязываются к его адаптеру."""
    return {"type": "direct", "tag": tag, "bind_interface": interface}


def server_group(servers: List[Dict[str, Any]], selected: str) -> List[Dict[str, Any]]:
    """Несколько серверов в одном конфиге: каждый со своим тегом (id профиля),
    «auto» — лучший по задержке (sing-box сам проверяет их и переключается),
    «proxy» — переключатель между «auto» и серверами (можно менять на лету через API)."""
    tags = [ob["tag"] for ob in servers]
    if selected not in tags and selected != AUTO_TAG:
        selected = tags[0]
    return servers + [
        {"type": "urltest", "tag": AUTO_TAG, "outbounds": tags, "url": URLTEST_URL, "interval": "1m",
         "tolerance": 50, "idle_timeout": "30m"},
        {"type": "selector", "tag": "proxy", "outbounds": [AUTO_TAG] + tags, "default": selected,
         "interrupt_exist_connections": True},
    ]


# Программы, которые часто пускают мимо VPN: онлайн-игры (задержка до игрового сервера важнее всего)
# и загрузки Steam. Имена .exe — без учёта регистра.
EXCLUDE_PRESETS: Dict[str, List[str]] = {
    "Dota 2": ["dota2.exe"],
    "Counter-Strike 2": ["cs2.exe"],
    "Valorant": ["valorant-win64-shipping.exe", "riotclientservices.exe"],
    "League of Legends": ["league of legends.exe", "leagueclient.exe"],
    "Fortnite": ["fortniteclient-win64-shipping.exe"],
    "Steam (загрузки игр)": ["steam.exe", "steamwebhelper.exe", "steamservice.exe"],
}


def clean_exe_names(names: List[str]) -> List[str]:
    """Имена программ: только имя файла, в нижнем регистре, с .exe, без повторов."""
    out: List[str] = []
    for raw in names:
        name = re.split(r"[\\/]", str(raw).strip().strip('"'))[-1].strip().lower()
        if not name:
            continue
        if not name.endswith(".exe"):
            name += ".exe"
        if name not in out:
            out.append(name)
    return out


def exclude_rule(names: List[str]) -> Optional[Dict[str, Any]]:
    """Правило «эти программы — напрямую». По пути без учёта регистра: в Windows имя файла может быть
    VALORANT-Win64-Shipping.exe, а process_name в sing-box сравнивает с учётом регистра."""
    names = clean_exe_names(names)
    if not names:
        return None
    return {"process_path_regex": ["(?i)(^|[\\\\/])" + re.escape(n) + "$" for n in names],
            "outbound": "direct"}


def build_config(proxy: Any, mode: str = "tun", ru_direct: bool = True,
                 proxy_port: int = DEFAULT_PROXY_PORT, rule_sets: Optional[Dict[str, Path]] = None,
                 bypass: Optional[List[str]] = None, log_level: str = "info",
                 api: Optional[Tuple[int, str]] = None, exclude_apps: Optional[List[str]] = None) -> Dict[str, Any]:
    """config.json для sing-box.

    proxy — исходящее «через VPN»: один outbound (VLESS-сервер или туннель AmneziaWG)
    или готовый список из server_group();
    api — (порт, secret) встроенного API для задержек и переключения серверов;
    ru_direct — российское напрямую: .ru/.su/.рф и базы roscomvpn (если скачаны);
    bypass — адреса, которые всегда идут мимо (сервер AmneziaWG — иначе петля);
    exclude_apps — программы мимо VPN (имена .exe; только «Весь трафик» — в режиме прокси они и так мимо)."""
    rule_sets = {tag: p for tag, p in (rule_sets or {}).items() if p and Path(p).exists()} if ru_direct else {}
    has = [t for t in RULE_SETS if t in rule_sets]
    bypass = [b if "/" in b else (b + "/128" if ":" in b else b + "/32") for b in (bypass or [])]

    dns_rules: List[Dict[str, Any]] = []
    route_rules: List[Dict[str, Any]] = [{"action": "sniff"}]
    if mode == "tun":
        route_rules.append({"protocol": "dns", "action": "hijack-dns"})
        excluded = exclude_rule(exclude_apps or [])
        if excluded:
            route_rules.append(excluded)
    if bypass:
        route_rules.append({"ip_cidr": bypass, "outbound": "direct"})
    route_rules.append({"ip_is_private": True, "outbound": "direct"})
    if ru_direct:
        proxy_sets = [t for t in PROXY_SETS if t in has]
        if proxy_sets:
            dns_rules.append({"rule_set": proxy_sets, "server": "dns-remote"})
            route_rules.append({"rule_set": proxy_sets, "outbound": "proxy"})
        dns_rules.append({"domain_suffix": RU_SUFFIXES, "server": "dns-local"})
        route_rules.append({"domain_suffix": RU_SUFFIXES, "outbound": "direct"})
        site_sets = [t for t in DIRECT_SITE_SETS if t in has]
        if site_sets:
            dns_rules.append({"rule_set": site_sets, "server": "dns-local"})
            route_rules.append({"rule_set": site_sets, "outbound": "direct"})
        ip_sets = [t for t in DIRECT_IP_SETS if t in has]
        if ip_sets:
            if mode != "tun":
                # прокси получает имя сайта, а не адрес: узнаём IP (через VPN — без подмены
                # ответов провайдером), чтобы сайты на российских адресах шли напрямую
                route_rules.append({"action": "resolve", "server": "dns-remote"})
            route_rules.append({"rule_set": ip_sets, "outbound": "direct"})

    if mode == "tun":
        inbound: Dict[str, Any] = {
            "type": "tun", "tag": "tun-in", "interface_name": INTERFACE_NAME,
            "address": [TUN_ADDRESS], "mtu": 9000, "auto_route": True, "strict_route": True,
            "stack": "mixed",
        }
        if bypass:
            inbound["route_exclude_address"] = bypass
    else:
        inbound = {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": int(proxy_port)}

    route: Dict[str, Any] = {
        "rules": route_rules,
        "final": "proxy",
        "auto_detect_interface": True,
        "default_domain_resolver": "dns-local",
    }
    if has:
        route["rule_set"] = [{"type": "local", "tag": tag, "format": "binary", "path": str(rule_sets[tag])}
                             for tag in has]
    outbounds = list(proxy) if isinstance(proxy, list) else [dict(proxy, tag="proxy")]
    cfg = {
        "log": {"level": log_level, "timestamp": True},
        "dns": {
            "servers": [
                {"type": "https", "tag": "dns-remote", "server": "1.1.1.1", "detour": "proxy"},
                {"type": "local", "tag": "dns-local"},
            ],
            "rules": dns_rules,
            "final": "dns-remote",
            "strategy": "ipv4_only",
        },
        "inbounds": [inbound],
        "outbounds": outbounds + [{"type": "direct", "tag": "direct"}],
        "route": route,
    }
    if api:
        cfg["experimental"] = {"clash_api": {"external_controller": f"127.0.0.1:{api[0]}", "secret": api[1]}}
    return cfg


# ====================================================================== релизы sing-box

def version_ok(version: str) -> bool:
    v = github.parse_version(version)[:2]
    return len(v) == 2 and MIN_VERSION <= v < MAX_VERSION


def asset_name(version: str) -> str:
    arch = winutil.machine_arch()
    if "ARM64" in arch:
        name = "arm64"
    elif arch.endswith("64"):
        name = "amd64"
    else:
        name = "386"
    legacy = winutil.IS_WINDOWS and winutil.windows_major_version() < 10 and name != "arm64"
    return f"sing-box-{version}-windows-{name}" + ("-legacy-windows-7" if legacy else "") + ".zip"


def pick_release(data: List[Dict[str, Any]]) -> Optional[github.Release]:
    """Самый новый стабильный релиз из поддерживаемого диапазона версий."""
    best: Optional[github.Release] = None
    for rel in data:
        tag = rel.get("tag_name") or ""
        if rel.get("draft") or rel.get("prerelease") or not version_ok(tag) or "-" in tag:
            continue
        assets = {}
        for a in rel.get("assets") or []:
            digest = a.get("digest") or ""
            assets[a["name"]] = github.Asset(a["name"], a["browser_download_url"],
                                             digest.split(":", 1)[1] if digest.startswith("sha256:") else None)
        cand = github.Release(REPO, tag, rel.get("html_url") or "", assets)
        if best is None or github.parse_version(cand.version) > github.parse_version(best.version):
            best = cand
    return best


def latest_release() -> github.Release:
    try:
        with github._open(f"https://api.github.com/repos/{REPO}/releases?per_page=40", 15,
                          "application/vnd.github+json") as resp:
            rel = pick_release(json.loads(resp.read().decode("utf-8")))
        if rel is not None:
            return rel
    except Exception as exc:
        log.debug("sing-box: GitHub API: %r — беру проверенную версию %s", exc, PINNED_VERSION)
    tag = f"v{PINNED_VERSION}"
    return github.Release(REPO, tag, f"https://github.com/{REPO}/releases/tag/{tag}")


# ====================================================================== управление

@dataclass
class VpnStatus:
    installed: bool = False
    version: str = ""
    running: bool = False
    pids: List[int] = field(default_factory=list)
    mode: str = ""
    foreign: List[str] = field(default_factory=list)


class VpnManager:
    def __init__(self, root: Path, log_file: Path):
        self.root = root
        self.log_file = log_file
        self._proc = None

    @property
    def exe(self) -> Path:
        return self.root / EXE_NAME

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def version_file(self) -> Path:
        return self.root / "version.txt"

    @property
    def rules_dir(self) -> Path:
        return self.root / "rules"

    @property
    def proxy_backup(self) -> Path:
        """Прежние настройки прокси Windows — чтобы вернуть их даже после сбоя."""
        return self.root / "proxy_backup.json"

    def is_installed(self) -> bool:
        return self.exe.exists()

    def local_version(self) -> str:
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def rule_set_paths(self) -> Dict[str, Path]:
        return {tag: self.rules_dir / f"{tag}.srs" for tag in RULE_SETS}

    # ---------------------------------------------------------- статус
    def _own_processes(self) -> List[winutil.ProcInfo]:
        return [p for p in winutil.find_processes(EXE_NAME) if p.exe and winutil.same_path(p.exe, self.exe)]

    def running_mode(self) -> str:
        try:
            cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            return "tun" if cfg["inbounds"][0]["type"] == "tun" else "proxy"
        except Exception:
            return ""

    def status(self) -> VpnStatus:
        st = VpnStatus(installed=self.is_installed(), version=self.local_version())
        for p in winutil.find_processes(EXE_NAME):
            if p.exe and winutil.same_path(p.exe, self.exe):
                st.pids.append(p.pid)
            else:
                st.foreign.append(p.exe or p.name)
        st.running = bool(st.pids)
        if st.running:
            st.mode = self.running_mode()
        return st

    # ---------------------------------------------------------- запуск
    def write_config(self, cfg: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.config_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.config_file)

    def check_config(self) -> None:
        res = winutil.run([str(self.exe), "check", "-c", str(self.config_file)], timeout=30, encoding="utf-8")
        if res.returncode != 0:
            text = _clean_log((res.stdout + res.stderr).strip())
            raise RuntimeError(f"sing-box не принял настройки:\n{text}")

    def start(self, proxy: Any, name: str, mode: str, ru_direct: bool, proxy_port: int,
              bypass: Optional[List[str]] = None, exclude_apps: Optional[List[str]] = None) -> None:
        """proxy — исходящее «через VPN»: outbound(VlessKey), awg_outbound(…) или server_group(…)."""
        if not self.is_installed():
            raise RuntimeError("Движок VPN (sing-box) ещё не скачан")
        self.stop()
        if ru_direct:
            self.update_rule_sets(only_missing=True)
        import secrets
        api = (_free_port(), secrets.token_hex(16))
        cfg = build_config(proxy, mode, ru_direct, proxy_port, self.rule_set_paths(), bypass, api=api,
                           exclude_apps=exclude_apps)
        self.write_config(cfg)
        self.check_config()
        if mode == "proxy" and winutil.port_listening(proxy_port):
            raise RuntimeError(f"Порт {proxy_port} уже занят другой программой — укажите другой порт VPN.")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_file, "wb")
        try:
            log.info("Запуск VPN: %s (%s)", name, MODES.get(mode, mode))
            self._proc = winutil.popen_hidden([str(self.exe), "run", "-c", str(self.config_file),
                                               "-D", str(self.root)], cwd=self.root, stdout=logf,
                                              detached=True)
        finally:
            logf.close()
        deadline = time.time() + (6 if mode == "tun" else 3)
        while time.time() < deadline:
            time.sleep(0.25)
            if self._proc.poll() is not None:
                tail = _clean_log(self.read_log_tail())
                self._proc = None
                raise RuntimeError(f"sing-box завершился сразу после запуска.\n{tail}")
            if mode == "proxy" and winutil.port_listening(proxy_port):
                break
        if mode == "proxy":
            self.enable_system_proxy(proxy_port)

    def stop(self) -> None:
        procs = self._own_processes()
        for p in procs:
            winutil.kill_pid_tree(p.pid)
        self._proc = None
        self.restore_system_proxy()
        if procs:
            log.info("VPN выключен")

    def read_log_tail(self, n: int = 15) -> str:
        try:
            lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    # ---------------------------------------------------------- встроенный API (задержки, выбор сервера)
    def _api(self) -> Optional[Tuple[str, str]]:
        try:
            cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            c = cfg["experimental"]["clash_api"]
            return c["external_controller"], c.get("secret", "")
        except Exception:
            return None

    def api_request(self, path: str, method: str = "GET", body: Optional[dict] = None,
                    timeout: float = 3.0) -> Any:
        from urllib.request import ProxyHandler, Request, build_opener
        api = self._api()
        if api is None:
            raise RuntimeError("API sing-box недоступно")
        data = json.dumps(body).encode() if body is not None else None
        req = Request(f"http://{api[0]}{path}", data=data, method=method,
                      headers={"Authorization": f"Bearer {api[1]}", "Content-Type": "application/json"})
        opener = build_opener(ProxyHandler({}))  # к 127.0.0.1 — без системного прокси
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def has_group(self) -> bool:
        """Запущен ли конфиг с группой серверов (VLESS, ручной выбор или «авто»)."""
        try:
            cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            return any(ob.get("tag") == AUTO_TAG for ob in cfg.get("outbounds", []))
        except Exception:
            return False

    def group_tags(self) -> List[str]:
        """id серверов в запущенном конфиге."""
        try:
            cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            return next((ob["outbounds"] for ob in cfg.get("outbounds", []) if ob.get("tag") == AUTO_TAG), [])
        except Exception:
            return []

    def group_state(self) -> Tuple[str, str, Dict[str, int]]:
        """(выбрано: «auto» или id сервера, используется сейчас: id сервера,
        последние задержки серверов в мс по проверкам sing-box)."""
        data = self.api_request("/proxies")["proxies"]
        selected = data.get("proxy", {}).get("now", "")
        actual = data.get(AUTO_TAG, {}).get("now", "") if selected == AUTO_TAG else selected
        delays: Dict[str, int] = {}
        for tag in data.get(AUTO_TAG, {}).get("all", []):
            hist = data.get(tag, {}).get("history") or []
            if hist and hist[-1].get("delay"):
                delays[tag] = int(hist[-1]["delay"])
        return selected, actual, delays

    def test_delays(self, timeout_ms: int = 5000) -> Dict[str, int]:
        """Проверить задержку всех серверов группы через сами серверы (реальная задержка)."""
        res = self.api_request(f"/group/{AUTO_TAG}/delay?url={URLTEST_URL}&timeout={timeout_ms}",
                               timeout=timeout_ms / 1000 + 3)
        return {k: int(v) for k, v in (res or {}).items() if v}

    def select_server(self, tag: str) -> None:
        """Переключить сервер на лету, без перезапуска."""
        self.api_request("/proxies/proxy", method="PUT", body={"name": tag})

    # ---------------------------------------------------------- системный прокси
    def enable_system_proxy(self, port: int) -> None:
        if not self.proxy_backup.exists():
            prev = winutil.get_system_proxy()
            self.proxy_backup.write_text(json.dumps(prev), encoding="utf-8")
        winutil.set_system_proxy(True, f"127.0.0.1:{port}", winutil.PROXY_BYPASS)
        log.info("Системный прокси Windows: 127.0.0.1:%s", port)

    def restore_system_proxy(self) -> None:
        """Возвращает прежние настройки прокси, если их меняли (в том числе после сбоя)."""
        if not self.proxy_backup.exists():
            return
        try:
            prev = json.loads(self.proxy_backup.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
        try:
            winutil.set_system_proxy(bool(prev.get("enable")), prev.get("server") or "",
                                     prev.get("override") or "")
            log.info("Настройки прокси Windows восстановлены")
        except Exception as exc:
            log.warning("Не удалось вернуть настройки прокси Windows: %r", exc)
            return
        self.proxy_backup.unlink(missing_ok=True)

    def recover(self) -> None:
        """После сбоя или выключения ПК: прокси указывает на выключенный VPN — вернуть как было."""
        if self.proxy_backup.exists() and not self._own_processes():
            self.restore_system_proxy()

    # ---------------------------------------------------------- обновление
    def update_rule_sets(self, only_missing: bool = False, max_age: float = RULES_MAX_AGE) -> None:
        """Скачивает базы для раздельного туннелирования (несколько небольших файлов)."""
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for tag, url in RULE_SETS.items():
            dest = self.rules_dir / f"{tag}.srs"
            if dest.exists() and (only_missing or now - dest.stat().st_mtime < max_age):
                continue
            try:
                github.download(url, dest)
            except Exception as exc:
                log.warning("VPN: не удалось скачать базу %s: %s", tag, exc)

    def install_release(self, release: github.Release, download_dir: Path,
                        progress: Optional[github.ProgressCb] = None) -> None:
        asset = release.asset(asset_name(release.version))
        zip_path = download_dir / asset.name
        github.download(asset.url, zip_path, progress, asset.sha256)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                member = next((n for n in zf.namelist() if n.lower().endswith("/" + EXE_NAME)
                               or n.lower() == EXE_NAME), None)
                if member is None:
                    raise IOError(f"в архиве {asset.name} нет {EXE_NAME}")
                self.root.mkdir(parents=True, exist_ok=True)
                new = self.root / (EXE_NAME + ".new")
                with zf.open(member) as src, open(new, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        finally:
            zip_path.unlink(missing_ok=True)
        was_running = self._own_processes()
        for p in was_running:
            winutil.kill_pid_tree(p.pid)
        for _ in range(10):
            try:
                new.replace(self.exe)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("не удалось заменить sing-box.exe — файл занят")
        self.version_file.write_text(release.version, encoding="utf-8")
        self.update_rule_sets(max_age=0)
        log.info("sing-box %s установлен (%s)", release.version, asset.name)
        if was_running:  # перезапуск с тем же конфигом
            logf = open(self.log_file, "wb")
            try:
                self._proc = winutil.popen_hidden([str(self.exe), "run", "-c", str(self.config_file),
                                                   "-D", str(self.root)], cwd=self.root, stdout=logf,
                                                  detached=True)
            finally:
                logf.close()


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _clean_log(text: str) -> str:
    return _ANSI.sub("", text)


def ping(key: VlessKey, timeout: float = 4.0) -> Optional[float]:
    """Время TCP-подключения к серверу, мс (None — не отвечает)."""
    import socket
    start = time.perf_counter()
    try:
        with socket.create_connection((key.server, key.port), timeout=timeout):
            return (time.perf_counter() - start) * 1000
    except OSError:
        return None
