"""AmneziaWG: ключи AmneziaVPN (vpn://…) и конфиги .conf.

Туннель запускает официальный клиент AmneziaWG для Windows
(amnezia-vpn/amneziawg-windows-client): из его установщика .msi берётся
amneziawg.exe, а BypassHub ставит туннель как службу командой
`amneziawg.exe /installtunnelservice <файл.conf>` — так же, как это делает сам
клиент. Отдельно ставить AmneziaWG или AmneziaVPN не нужно.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import github, winutil
from .log import log

REPO = "amnezia-vpn/amneziawg-windows-client"
EXE_NAME = "amneziawg.exe"
TUNNEL_NAME = "BypassHub"                 # имя туннеля = имя сетевого адаптера
SERVICE_NAME = f"AmneziaWGTunnel${TUNNEL_NAME}"
START_TIMEOUT = 60                        # сколько ждать запуска службы туннеля, с
FALLBACK_METRIC = 9000
WINTUN_DLL = "wintun.dll"  # драйвер сетевого адаптера; служба загружает его из папки amneziawg.exe


# ====================================================================== ключи

def _b64decode(text: str) -> bytes:
    compact = re.sub(r"\s+", "", text)
    compact += "=" * (-len(compact) % 4)
    try:
        return base64.urlsafe_b64decode(compact)
    except (binascii.Error, ValueError):
        return base64.b64decode(compact)


def decode_amnezia_key(key: str) -> Dict[str, Any]:
    """vpn://… → JSON AmneziaVPN. Формат Qt: base64url(4 байта длины + zlib) или просто JSON."""
    body = key.strip()
    if body.lower().startswith("vpn://"):
        body = body[6:]
    try:
        raw = _b64decode(body)
    except (binascii.Error, ValueError):
        raise ValueError("Ключ AmneziaVPN повреждён — скопируйте его целиком") from None
    for candidate in (raw[4:], raw):
        try:
            raw = zlib.decompress(candidate)
            break
        except zlib.error:
            continue
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("Ключ AmneziaVPN повреждён — скопируйте его целиком") from None
    if not isinstance(data, dict):
        raise ValueError("Неизвестный формат ключа AmneziaVPN")
    return data


def conf_from_amnezia_key(key: str) -> "AwgProfile":
    data = decode_amnezia_key(key)
    name = str(data.get("description") or data.get("hostName") or "AmneziaWG").strip()
    dns = [str(data.get(k) or "").strip() for k in ("dns1", "dns2")]
    for container in data.get("containers") or []:
        if not isinstance(container, dict):
            continue
        for proto, value in container.items():
            if not isinstance(value, dict) or "last_config" not in value:
                continue
            try:
                last = json.loads(value["last_config"]) if isinstance(value["last_config"], str) \
                    else value["last_config"]
            except ValueError:
                continue
            conf = (last or {}).get("config") if isinstance(last, dict) else None
            if not conf or "[Interface]" not in conf:
                continue
            if proto not in ("awg", "wireguard"):
                raise ValueError(f"В ключе протокол «{proto}» — BypassHub поддерживает AmneziaWG и VLESS")
            conf = _fill_placeholders(conf, dns)
            mtu = str(last.get("mtu") or "").strip()
            if mtu.isdigit() and not re.search(r"(?im)^\s*MTU\s*=", conf):
                conf = re.sub(r"(?im)^(\s*\[Interface\]\s*)$", rf"\1\nMTU = {mtu}", conf, count=1)
            return parse_conf(conf, name)
    if any(k in data for k in ("api_config", "auth_data", "api_key")):
        raise ValueError("Это ключ подписки Amnezia Premium — в нём нет настроек сервера. "
                         "Подойдёт ключ от своего сервера (AmneziaVPN → «Поделиться»).")
    raise ValueError("В ключе нет настроек AmneziaWG. В AmneziaVPN откройте «Поделиться» и выберите "
                     "протокол AmneziaWG.")


def _fill_placeholders(conf: str, dns: List[str]) -> str:
    primary = dns[0] or "1.1.1.1"
    secondary = dns[1] or ""
    conf = conf.replace("$PRIMARY_DNS", primary).replace("$SECONDARY_DNS", secondary)
    # «DNS = 1.1.1.1, » без второго адреса
    return re.sub(r"(?im)^(\s*DNS\s*=.*?)[,\s]+$", r"\1", conf)


@dataclass
class AwgProfile:
    name: str
    conf: str
    endpoint: str = ""
    address: str = ""
    extras: List[str] = field(default_factory=list)  # параметры обфускации (Jc, S1, H1, …)

    @property
    def summary(self) -> str:
        return f"{self.endpoint} · AmneziaWG" + (f" · {self.address}" if self.address else "")


def _sections(conf: str) -> Dict[str, List[Dict[str, str]]]:
    result: Dict[str, List[Dict[str, str]]] = {}
    current: Optional[Dict[str, str]] = None
    for line in conf.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"\[(\w+)\]", line)
        if m:
            current = {}
            result.setdefault(m.group(1).lower(), []).append(current)
        elif "=" in line and current is not None:
            k, v = line.split("=", 1)
            current[k.strip().lower()] = v.strip()
    return result


def parse_conf(text: str, name: str = "") -> AwgProfile:
    conf = text.replace("\r\n", "\n").strip() + "\n"
    sec = _sections(conf)
    iface = (sec.get("interface") or [{}])[0]
    peers = sec.get("peer") or []
    if not iface.get("privatekey"):
        raise ValueError("В конфиге нет [Interface] с PrivateKey")
    if not peers or not peers[0].get("publickey") or not peers[0].get("endpoint"):
        raise ValueError("В конфиге нет [Peer] с PublicKey и Endpoint")
    extras = [k for k in ("jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4", "i1")
              if k in iface]
    endpoint = peers[0]["endpoint"]
    return AwgProfile(name=name.strip() or endpoint.rsplit(":", 1)[0], conf=conf, endpoint=endpoint,
                      address=iface.get("address", ""), extras=extras)


def endpoint_ips(profile: "AwgProfile") -> List[str]:
    """IP-адреса сервера (Endpoint может быть доменом) — они всегда идут мимо sing-box."""
    import socket
    host = profile.endpoint.rsplit(":", 1)[0].strip("[]")
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise RuntimeError(f"Не удалось узнать адрес сервера AmneziaWG «{host}»: {exc}") from None
    return sorted({i[4][0] for i in infos})


def looks_like_conf(text: str) -> bool:
    return "[interface]" in text.lower() and "[peer]" in text.lower()


def prepare_conf(conf: str, lan_access: bool = True, split: bool = False) -> str:
    """Конфиг для туннеля.

    lan_access: 0.0.0.0/0 заменяется двумя половинами — трафик по-прежнему весь идёт
    в VPN, но клиент не включает блокировку «мимо туннеля», и остаётся доступ
    к локальной сети (принтер, телефон для TG WS Proxy).
    split: раздельное туннелирование — туннель поднимается без своих маршрутов и DNS
    (Table = off), а что пускать в него, решает sing-box."""
    if split:
        lines = [ln for ln in conf.splitlines() if not re.match(r"(?i)\s*(DNS|Table)\s*=", ln)]
        out: List[str] = []
        for ln in lines:
            out.append(ln)
            if re.fullmatch(r"(?i)\s*\[Interface\]\s*", ln):
                out.append("Table = off")
        return "\n".join(out) + "\n"
    if not lan_access:
        return conf

    def fix(m: "re.Match[str]") -> str:
        nets = [n.strip() for n in m.group(2).split(",") if n.strip()]
        out: List[str] = []
        for n in nets:
            if n == "0.0.0.0/0":
                out += ["0.0.0.0/1", "128.0.0.0/1"]
            elif n == "::/0":
                out += ["::/1", "8000::/1"]
            else:
                out.append(n)
        return m.group(1) + ", ".join(out)
    return re.sub(r"(?im)^(\s*AllowedIPs\s*=\s*)(.*)$", fix, conf)


# ====================================================================== релизы клиента

def asset_name(version: str) -> str:
    arch = winutil.machine_arch()
    name = "arm64" if "ARM64" in arch else ("amd64" if arch.endswith("64") else "x86")
    return f"amneziawg-{name}-{version}.msi"


def latest_release() -> github.Release:
    return github.latest_release(REPO)


# ====================================================================== управление

@dataclass
class AwgStatus:
    installed: bool = False
    version: str = ""
    running: bool = False
    state: Optional[str] = None


class AwgManager:
    def __init__(self, root: Path):
        self.root = root

    @property
    def exe(self) -> Path:
        return self.root / EXE_NAME

    @property
    def conf_file(self) -> Path:
        return self.root / f"{TUNNEL_NAME}.conf"

    @property
    def version_file(self) -> Path:
        return self.root / "version.txt"

    def is_installed(self) -> bool:
        # без wintun.dll рядом служба туннеля сразу останавливается (так было в 1.3.0 до исправления)
        return self.exe.exists() and (self.root / WINTUN_DLL).exists()

    def local_version(self) -> str:
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def status(self) -> AwgStatus:
        state = winutil.service_state(SERVICE_NAME)
        return AwgStatus(installed=self.is_installed(), version=self.local_version(),
                         running=state in ("RUNNING", "START_PENDING"), state=state)

    def start(self, profile: AwgProfile, lan_access: bool = True, split: bool = False) -> None:
        if not self.is_installed():
            raise RuntimeError("Клиент AmneziaWG ещё не скачан")
        self.stop()
        self._wait_adapter_gone()
        self.root.mkdir(parents=True, exist_ok=True)
        # Файл читает служба SYSTEM; права на папку — как у всей папки BypassHub
        self.conf_file.write_text(prepare_conf(profile.conf, lan_access, split), encoding="utf-8")
        log.info("Запуск AmneziaWG: %s (%s)", profile.name, profile.endpoint)
        res = winutil.run([str(self.exe), "/installtunnelservice", str(self.conf_file)], timeout=60,
                          encoding="utf-8")
        if res.returncode != 0:
            raise RuntimeError("AmneziaWG не запустился: " + ((res.stdout + res.stderr).strip() or
                                                              f"код {res.returncode}"))
        # служба создаёт сетевой адаптер Wintun; на части ПК это занимает десятки секунд
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            state = winutil.service_state(SERVICE_NAME)
            if state == "RUNNING":
                if split:
                    self._add_fallback_route()
                return
            if state in (None, "STOPPED"):
                break
            time.sleep(0.25)
        state = winutil.service_state(SERVICE_NAME)
        if state != "RUNNING":
            details = self.service_log_tail()
            self.stop()
            raise RuntimeError("Туннель AmneziaWG не запустился (служба: "
                               f"{state or 'нет'}). Если открыт AmneziaVPN, отключитесь в нём и "
                               "попробуйте снова." + (f"\n\nЖурнал AmneziaWG:\n{details}" if details else ""))

    @staticmethod
    def _wait_adapter_gone(timeout: float = 10) -> None:
        """Адаптер прежнего туннеля удаляется не сразу; пока он есть, новый с тем же именем
        создаётся очень долго (служба висит в START_PENDING на «Creating adapter»)."""
        try:
            import psutil
        except ImportError:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if TUNNEL_NAME not in psutil.net_if_stats():
                    return
            except Exception:
                return
            time.sleep(0.25)
        log.warning("AmneziaWG: адаптер прежнего туннеля ещё не удалён")

    def service_log_tail(self, lines: int = 8) -> str:
        """Последние строки журнала службы туннеля — там причина, если туннель не запустился."""
        try:
            res = winutil.run([str(self.exe), "/dumplog"], timeout=15, encoding="utf-8")
        except Exception as exc:
            log.debug("amneziawg /dumplog: %r", exc)
            return ""
        out = [ln for ln in res.stdout.splitlines() if ln.strip()]
        ours = [ln for ln in out if TUNNEL_NAME in ln] or out
        text = "\n".join(ours[-lines:])
        if text:
            log.warning("Журнал AmneziaWG:\n%s", text)
        return text

    def _add_fallback_route(self) -> None:
        """Маршрут по умолчанию через туннель с самым низким приоритетом. Обычный трафик
        по нему не идёт (у основного подключения приоритет выше), но соединения,
        которые sing-box привязывает к адаптеру туннеля, получают маршрут."""
        cmd = ["netsh", "interface", "ipv4", "add", "route", "0.0.0.0/0", TUNNEL_NAME,
               f"metric={FALLBACK_METRIC}", "store=active"]
        for _ in range(20):  # адаптер появляется не сразу после запуска службы
            if winutil.run_logged(cmd, timeout=15) == 0:
                return
            time.sleep(0.25)
        log.warning("AmneziaWG: не удалось добавить маршрут через туннель")

    def stop(self) -> None:
        if winutil.service_state(SERVICE_NAME) is None:
            return
        if self.exe.exists():
            winutil.run_logged([str(self.exe), "/uninstalltunnelservice", TUNNEL_NAME], timeout=60)
        for _ in range(40):
            if winutil.service_state(SERVICE_NAME) is None:
                break
            time.sleep(0.25)
        else:  # клиент не справился (служба зависла) — останавливаем и удаляем её сами
            winutil.service_stop_delete(SERVICE_NAME)
        log.info("AmneziaWG выключен")

    def install_release(self, release: github.Release, download_dir: Path,
                        progress: Optional[github.ProgressCb] = None) -> None:
        """Скачивает .msi и достаёт из него amneziawg.exe и wintun.dll (msiexec /a — распаковка без установки)."""
        asset = release.asset(asset_name(release.version))
        msi = download_dir / asset.name
        github.download(asset.url, msi, progress, asset.sha256)
        extract = download_dir / "amneziawg_extract"
        shutil.rmtree(extract, ignore_errors=True)
        try:
            res = winutil.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={extract}"], timeout=180)
            found = next(iter(extract.rglob(EXE_NAME)), None) if extract.exists() else None
            if found is None:
                raise RuntimeError(f"не удалось распаковать {asset.name} (msiexec: код {res.returncode})")
            running = self.status().running
            if running:
                self.stop()
            files = [found] + [f for f in found.parent.iterdir()
                               if f.is_file() and f.suffix.lower() in (".dll", ".exe") and f != found]
            if not any(f.name.lower() == WINTUN_DLL for f in files):
                raise RuntimeError(f"в {asset.name} нет {WINTUN_DLL}")
            self.root.mkdir(parents=True, exist_ok=True)
            for src in files:
                dest = self.root / src.name.lower()
                for _ in range(10):
                    try:
                        shutil.copy2(src, dest)
                        break
                    except OSError:
                        time.sleep(0.5)
                else:
                    raise RuntimeError(f"не удалось заменить {src.name} — файл занят")
        finally:
            msi.unlink(missing_ok=True)
            shutil.rmtree(extract, ignore_errors=True)
        self.version_file.write_text(release.version, encoding="utf-8")
        log.info("AmneziaWG %s установлен (%s)", release.version, asset.name)
        if running and self.conf_file.exists():
            winutil.run_logged([str(self.exe), "/installtunnelservice", str(self.conf_file)], timeout=60)
