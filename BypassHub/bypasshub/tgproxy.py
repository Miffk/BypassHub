"""Управление tg-ws-proxy (Flowseal).

Скачивается официальный exe из релизов и запускается в портативном режиме:
рядом с ним лежит папка TgWsProxy_data, поэтому его config.json и логи живут
внутри папки BypassHub и не пересекаются с отдельно установленной копией.
Все настройки из окна «Настройки» tg-ws-proxy редактируются здесь и
записываются в этот config.json.
"""
from __future__ import annotations

import json
import math
import os
import socket
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import github, winutil
from .log import log

REPO = "Flowseal/tg-ws-proxy"
EXE_NAME = "TgWsProxy.exe"
PORTABLE_DIR = "TgWsProxy_data"

ASSETS = {
    "TgWsProxy_windows.exe": "Windows 10/11 x64",
    "TgWsProxy_windows_arm64.exe": "Windows 10/11 ARM64",
    "TgWsProxy_windows_7_64bit.exe": "Windows 7 x64",
    "TgWsProxy_windows_7_32bit.exe": "Windows 7 x32",
}

# Совпадает с utils/default_config.py из tg-ws-proxy
DEFAULT_CONFIG: Dict[str, Any] = {
    "port": 1443,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
    "check_updates": False,   # обновлениями занимается BypassHub
    "log_max_mb": 5,
    "buf_kb": 256,
    "pool_size": 4,
    "cfproxy": True,
    "h2": True,
    "cfproxy_user_domain_enabled": False,
    "cfproxy_user_domain": [],
    "cfproxy_worker_enabled": False,
    "cfproxy_worker_domain": [],
    "force_test_dc": False,
    "no_secure": False,
    "appearance": "auto",
    "language": "ru",
    "autostart": False,       # автозапуском занимается BypassHub
}


def new_secret() -> str:
    return os.urandom(16).hex()


def coerce_domain_list(value) -> List[str]:
    if isinstance(value, str):
        items = value.replace(",", " ").replace(";", " ").split()
    elif isinstance(value, (list, tuple)):
        items = []
        for entry in value:
            if isinstance(entry, str):
                items.extend(entry.replace(",", " ").replace(";", " ").split())
    else:
        return []
    seen, result = set(), []
    for item in items:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def parse_dc_ip(lines: List[str]) -> Dict[int, str]:
    result: Dict[int, str] = {}
    for entry in lines:
        if ":" not in entry:
            raise ValueError(f"Неверный формат «{entry}», нужно DC:IP (например 2:149.154.167.220)")
        dc_s, ip_s = entry.split(":", 1)
        try:
            dc = int(dc_s)
            socket.inet_pton(socket.AF_INET, ip_s)
        except (ValueError, OSError):
            raise ValueError(f"Неверное правило DC → IP: «{entry}»") from None
        result[dc] = ip_s
    return result


def validate(values: Dict[str, Any]) -> Union[Dict[str, Any], str]:
    """Проверка значений формы (логика из ui/settings.py tg-ws-proxy).
    Возвращает готовый конфиг или текст ошибки."""
    cfg = dict(values)
    host = str(values.get("host", "")).strip()
    try:
        socket.inet_aton(host)
        if host.count(".") != 3:
            raise OSError
    except OSError:
        return "Неверный IP-адрес"
    try:
        port = int(str(values.get("port", "")).strip())
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return "Порт должен быть числом от 1 до 65535"
    dc_raw = values.get("dc_ip", "")
    lines = dc_raw if isinstance(dc_raw, list) else str(dc_raw).splitlines()
    lines = [ln.strip() for ln in lines if ln.strip()]
    try:
        parse_dc_ip(lines)
    except ValueError as exc:
        return str(exc)
    secret = str(values.get("secret", "")).strip()
    if len(secret) != 32:
        return "Secret должен состоять ровно из 32 hex-символов"
    if any(c not in "0123456789abcdefABCDEF" for c in secret):
        return "Secret может содержать только символы 0-9 и a-f"
    cfg.update(host=host, port=port, secret=secret.lower(), dc_ip=lines)
    for key in ("buf_kb", "pool_size", "log_max_mb"):
        try:
            value = float(str(values.get(key, "")).strip())
            if not math.isfinite(value) or value < 0:
                raise ValueError
            cfg[key] = value if key == "log_max_mb" else int(value)
        except (ValueError, OverflowError):
            cfg[key] = DEFAULT_CONFIG[key]
    for key in ("cfproxy_user_domain", "cfproxy_worker_domain"):
        cfg[key] = coerce_domain_list(cfg.get(key, []))
    return cfg


def proxy_link(cfg: Dict[str, Any]) -> str:
    host = cfg.get("host", DEFAULT_CONFIG["host"])
    if host == "0.0.0.0":
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                host = s.getsockname()[0]
        except OSError:
            host = "127.0.0.1"
    return f"tg://proxy?server={host}&port={cfg.get('port', 1443)}&secret=dd{cfg.get('secret', '')}"


def choose_asset(preference: str = "auto") -> str:
    if preference in ASSETS:
        return preference
    if winutil.IS_WINDOWS and winutil.windows_major_version() < 10:
        return "TgWsProxy_windows_7_64bit.exe" if winutil.machine_arch().endswith("64") \
            else "TgWsProxy_windows_7_32bit.exe"
    if "ARM64" in winutil.machine_arch():
        return "TgWsProxy_windows_arm64.exe"
    return "TgWsProxy_windows.exe"


@dataclass
class TgStatus:
    installed: bool = False
    version: str = ""
    running: bool = False
    listening: bool = False
    pids: List[int] = field(default_factory=list)
    foreign: List[str] = field(default_factory=list)


class TgProxyManager:
    def __init__(self, root: Path):
        self.root = root

    @property
    def exe(self) -> Path:
        return self.root / EXE_NAME

    @property
    def data_dir(self) -> Path:
        return self.root / PORTABLE_DIR

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "proxy.log"

    @property
    def version_file(self) -> Path:
        return self.root / "version.txt"

    def is_installed(self) -> bool:
        return self.exe.exists()

    def local_version(self) -> str:
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # ---------------------------------------------------------- конфиг
    def load_config(self) -> Dict[str, Any]:
        cfg = deepcopy(DEFAULT_CONFIG)
        data: Dict[str, Any] = {}
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning("tg-ws-proxy: не удалось прочитать config.json: %r", exc)
        if "cfproxy_user_domain_enabled" not in data and "cfproxy_user_domain" in data:
            data["cfproxy_user_domain_enabled"] = bool(coerce_domain_list(data.get("cfproxy_user_domain")))
        if "cfproxy_worker_enabled" not in data and "cfproxy_worker_domain" in data:
            data["cfproxy_worker_enabled"] = bool(coerce_domain_list(data.get("cfproxy_worker_domain")))
        cfg.update(data)
        if not cfg.get("secret"):
            cfg["secret"] = new_secret()
        cfg["check_updates"] = False
        cfg["autostart"] = False
        return cfg

    def save_config(self, cfg: Dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = dict(cfg)
        data["check_updates"] = False
        data["autostart"] = False
        tmp = self.config_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.config_file)

    def ensure_config(self) -> Dict[str, Any]:
        cfg = self.load_config()
        self.save_config(cfg)
        # не показывать окно первого запуска и предупреждение об IPv6 —
        # подключение к Telegram настраивается из BypassHub
        for marker in (".first_run_done_mtproto", ".ipv6_warned"):
            (self.data_dir / marker).touch(exist_ok=True)
        return cfg

    # ---------------------------------------------------------- статус
    def _own_processes(self) -> List[winutil.ProcInfo]:
        return [p for p in winutil.find_processes_like("tgwsproxy")
                if p.exe and winutil.same_path(p.exe, self.exe)]

    def status(self) -> TgStatus:
        st = TgStatus(installed=self.is_installed(), version=self.local_version())
        for p in winutil.find_processes_like("tgwsproxy"):
            if p.exe and winutil.same_path(p.exe, self.exe):
                st.pids.append(p.pid)
            elif p.exe:
                st.foreign.append(p.exe)
        st.running = bool(st.pids)
        if st.running:
            cfg = self.load_config()
            st.listening = winutil.port_listening(int(cfg.get("port", 1443)), cfg.get("host", "127.0.0.1"))
        return st

    # ---------------------------------------------------------- запуск
    def start(self) -> None:
        if not self.is_installed():
            raise RuntimeError("tg-ws-proxy ещё не скачан — нажмите «Проверить обновления»")
        if self._own_processes():
            return
        foreign = [p for p in winutil.find_processes_like("tgwsproxy")
                   if not (p.exe and winutil.same_path(p.exe, self.exe))]
        if foreign:
            raise RuntimeError("Уже запущен другой TgWsProxy:\n" + "\n".join(p.exe or p.name for p in foreign)
                               + "\nЗакройте его (значок в трее → Выход) и попробуйте снова.")
        self.ensure_config()
        log.info("Запуск tg-ws-proxy")
        winutil.popen_hidden([str(self.exe)], cwd=self.root, detached=True)
        for _ in range(20):
            time.sleep(0.25)
            if self._own_processes():
                return
        raise RuntimeError("tg-ws-proxy не запустился. Подробности — в логе прокси.")

    def stop(self) -> None:
        procs = self._own_processes()
        # PyInstaller onefile: родительский загрузчик + дочерний процесс
        for p in procs:
            winutil.kill_pid_tree(p.pid)
        if procs:
            log.info("tg-ws-proxy остановлен")

    def restart(self) -> None:
        self.stop()
        time.sleep(1.0)
        self.start()

    # ---------------------------------------------------------- обновление
    def install_release(self, release: github.Release, asset_name: str, download_dir: Path,
                        progress: Optional[github.ProgressCb] = None) -> None:
        asset = release.asset(asset_name)
        tmp = download_dir / asset_name
        github.download(asset.url, tmp, progress, asset.sha256)
        if tmp.stat().st_size < 1024 * 1024:
            raise IOError("скачанный файл слишком маленький — похоже, это не exe")
        was_running = bool(self._own_processes())
        self.stop()
        self.root.mkdir(parents=True, exist_ok=True)
        old = self.exe.with_suffix(".old")
        if self.exe.exists():
            old.unlink(missing_ok=True)
            for _ in range(10):
                try:
                    self.exe.rename(old)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("не удалось заменить TgWsProxy.exe — файл занят")
        try:
            tmp.replace(self.exe)
        except Exception:
            if old.exists() and not self.exe.exists():
                old.rename(self.exe)
            raise
        old.unlink(missing_ok=True)
        self.version_file.write_text(release.version, encoding="utf-8")
        self.ensure_config()
        log.info("tg-ws-proxy %s установлен (%s)", release.version, asset_name)
        if was_running:
            self.start()
