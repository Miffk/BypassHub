"""Экспериментальное исключение программ по IP-адресам.

Раз в несколько секунд смотрит (в локальной таблице соединений Windows, без
сетевых запросов), к каким адресам подключены процессы выбранных программ, и
дописывает эти адреса в ipset-exclude-user.txt. winws сам перечитывает список
при изменении файла, перезапуск обхода не нужен.

Ограничение: первое подключение к новому адресу успевает пройти через обход.
Адреса, на которых работают Discord и YouTube, не исключаются никогда — они
бывают общими у CDN.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Set

from .log import log

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

APP_PROCESSES: Dict[str, List[str]] = {
    "Steam": ["steam.exe", "steamwebhelper.exe", "steamservice.exe"],
    "Epic Games": ["epicgameslauncher.exe", "epicwebhelper.exe"],
    "Battle.net": ["battle.net.exe", "agent.exe"],
    "Riot Games": ["riotclientservices.exe", "riotclientux.exe", "leagueclient.exe", "valorant-win64-shipping.exe"],
    "EA app": ["eadesktop.exe", "eabackgroundservice.exe"],
    "Ubisoft Connect": ["upc.exe", "ubisoftconnect.exe", "uplaywebcore.exe"],
    "FACEIT": ["faceit.exe", "faceitclient.exe"],
    "Xbox / Microsoft Store": ["xboxpcapp.exe", "gamingservices.exe"],
}
PROTECTED_DOMAINS = [
    "discord.com", "gateway.discord.gg", "cdn.discordapp.com", "media.discordapp.net",
    "www.youtube.com", "i.ytimg.com", "redirector.googlevideo.com", "www.google.com",
]
BEGIN = "# >>> BypassHub: learned app addresses >>>"
END = "# <<< BypassHub: learned app addresses <<<"
MAX_ENTRIES = 3000


def split_block(text: str) -> tuple:
    outside, learned, inside = [], [], False
    for line in text.splitlines():
        s = line.strip()
        if s == BEGIN:
            inside = True
        elif s == END:
            inside = False
        elif inside:
            if s and not s.startswith("#"):
                learned.append(s)
        else:
            outside.append(line)
    return outside, learned


def write_block(path: Path, learned: Iterable[str], placeholder: str) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    outside, _ = split_block(text)
    lines = [ln for ln in outside if ln.strip()]
    learned = list(learned)
    if learned:
        lines += [BEGIN] + learned + [END]
    if not any(ln.strip() and not ln.strip().startswith("#") for ln in lines):
        lines = [placeholder]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)  # атомарно: winws не увидит недописанный файл


def is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_global


class AppLearner:
    INTERVAL = 8.0

    def __init__(self, exclude_file: Path, placeholder: str = "203.0.113.113/32"):
        self.file = exclude_file
        self.placeholder = placeholder
        self.apps: List[str] = []
        self._stop = threading.Event()
        self._thread = None
        self._protected: Set[str] = set()
        self._protected_at = 0.0

    def learned(self) -> List[str]:
        try:
            return split_block(self.file.read_text(encoding="utf-8", errors="replace"))[1]
        except OSError:
            return []

    def clear(self) -> None:
        if self.file.exists():
            write_block(self.file, [], self.placeholder)
        log.info("Выученные адреса программ очищены")

    def start(self, apps: List[str]) -> None:
        self.apps = [a for a in apps if a in APP_PROCESSES]
        if self._thread and self._thread.is_alive():
            return
        if psutil is None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="app-learn")
        self._thread.start()
        log.info("Обучение исключений запущено: %s", ", ".join(self.apps) or "нет программ")

    def stop(self) -> None:
        self._stop.set()

    def _refresh_protected(self) -> None:
        if time.time() - self._protected_at < 1800:
            return
        ips: Set[str] = set()
        for host in PROTECTED_DOMAINS:
            try:
                for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
                    ips.add(info[4][0])
            except OSError:
                pass
        self._protected, self._protected_at = ips, time.time()

    def scan_once(self) -> int:
        names = {n for app in self.apps for n in APP_PROCESSES[app]}
        pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() in names}
        if not pids or not self.file.parent.exists():
            return 0
        self._refresh_protected()
        found: Set[str] = set()
        for c in psutil.net_connections(kind="inet"):
            if c.pid in pids and c.raddr and c.status == psutil.CONN_ESTABLISHED \
                    and c.raddr.port in (80, 443) and is_public(c.raddr.ip) and c.raddr.ip not in self._protected:
                found.add(c.raddr.ip)
        known = self.learned()
        new = [ip for ip in sorted(found) if ip not in set(known)]
        if not new:
            return 0
        merged = (known + new)[-MAX_ENTRIES:]
        write_block(self.file, merged, self.placeholder)
        log.info("Исключены новые адреса программ: %s", ", ".join(new[:5]) + (" …" if len(new) > 5 else ""))
        return len(new)

    def _loop(self) -> None:
        while not self._stop.wait(self.INTERVAL):
            if not self.apps:
                continue
            try:
                self.scan_once()
            except Exception as exc:
                log.debug("app-learn: %r", exc)
