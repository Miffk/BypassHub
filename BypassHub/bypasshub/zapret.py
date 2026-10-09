"""Управление zapret-discord-youtube (Flowseal).

Всё, что умеет service.bat, перенесено сюда, а файлы настроек остаются теми
же самыми (utils/game_filter.enabled, lists/*-user.txt, ipset-all.txt,
bin/ACTIVE_*.bin), поэтому папкой zapret можно пользоваться и вручную.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import github, winutil
from .log import log

REPO = "Flowseal/zapret-discord-youtube"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
VERSION_URL = f"{RAW}/.service/version.txt"
IPSET_URL = f"{RAW}/.service/ipset-service.txt"
HOSTS_URL = f"{RAW}/.service/hosts"

SERVICE_NAME = "zapret"
SERVICE_REG_KEY = r"System\CurrentControlSet\Services\zapret"
SERVICE_REG_VALUE = "zapret-discord-youtube"
IPSET_NONE_MARK = "203.0.113.113/32"
GAME_FILTER_OFF = "12"  # так service.bat «выключает» диапазон портов

USER_LISTS: Dict[str, Tuple[str, str]] = {
    # файл: (описание, содержимое по умолчанию — файл нельзя оставлять пустым)
    "list-general-user.txt": ("Домены для обхода (поддомены учитываются автоматически)",
                              "# Never leave this file empty\ndomain.example.abc\n"),
    "list-exclude-user.txt": ("Домены-исключения (обход к ним не применяется)",
                              "domain.example.abc\n"),
    "ipset-exclude-user.txt": ("IP/подсети-исключения",
                               "203.0.113.113/32\n"),
}

FAKE_TARGETS = {
    "discord": ("ACTIVE_DISCORD_UDP.bin", "Discord UDP"),
    "game": ("ACTIVE_GAME_UDP.bin", "Game Filter UDP"),
}

GAME_MODES = ("disabled", "all", "tcp", "udp")

# Домены программ, к которым обход можно не применять. winws работает на уровне
# сетевых пакетов и не знает, какой программе они принадлежат, поэтому
# «исключение программы» = исключение её доменов (list-exclude-user.txt).
APP_EXCLUSIONS: Dict[str, List[str]] = {
    "Steam": ["steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcontent.com",
              "steamserver.net", "steamusercontent.com", "steam-chat.com", "steamgames.com",
              "valvesoftware.com", "steamcdn-a.akamaihd.net"],
    "Epic Games": ["epicgames.com", "epicgames.dev", "unrealengine.com", "fortnite.com",
                   "epicgames.net"],
    "Battle.net": ["battle.net", "blizzard.com", "battlenet.com.cn"],
    "Riot Games": ["riotgames.com", "leagueoflegends.com", "playvalorant.com", "riotcdn.net", "pvp.net"],
    "EA app": ["ea.com", "origin.com", "tnt-ea.com"],
    "Ubisoft Connect": ["ubi.com", "ubisoft.com", "ubisoftconnect.com"],
    "FACEIT": ["faceit.com", "faceit-cdn.net"],
    "Xbox / Microsoft Store": ["xboxlive.com", "xbox.com", "gamepass.com"],
}
APP_BLOCK_BEGIN = "# >>> BypassHub: исключённые программы >>>"
APP_BLOCK_END = "# <<< BypassHub: исключённые программы <<<"


# ======================================================================
# Разбор .bat стратегий
# ======================================================================

_VAR_RE = re.compile(r"%(~dp0|[A-Za-z_][A-Za-z0-9_]*)%?", re.IGNORECASE)


def _join_continuations(text: str) -> List[str]:
    lines: List[str] = []
    buf = ""
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.endswith("^") and not raw.endswith("^^"):
            buf += raw[:-1]
            continue
        lines.append(buf + raw)
        buf = ""
    if buf:
        lines.append(buf)
    return lines


def expand_vars(text: str, variables: Dict[str, str], missing: Optional[List[str]] = None) -> str:
    lowered = {k.lower(): v for k, v in variables.items()}
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        if text.startswith("%%", i):
            out.append("%")
            i += 2
            continue
        if text.startswith("%~dp0", i):
            out.append(lowered.get("~dp0", ""))
            i += 5
            continue
        end = text.find("%", i + 1)
        name = text[i + 1:end] if end != -1 else ""
        if end == -1 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            out.append(ch)
            i += 1
            continue
        value = lowered.get(name.lower())
        if value is None:
            if missing is not None:
                missing.append(name)
            value = ""
        out.append(value)
        i = end + 1
    return "".join(out)


def split_cmd_args(text: str) -> List[str]:
    """Разбиение строки так же, как это делает cmd.exe + CommandLineToArgv:
    кавычки группируют и удаляются, ^ вне кавычек экранирует следующий символ."""
    args: List[str] = []
    cur: List[str] = []
    in_quotes = False
    has_token = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_quotes = not in_quotes
            has_token = True
        elif ch == "^" and not in_quotes and i + 1 < len(text):
            i += 1
            cur.append(text[i])
            has_token = True
        elif ch in " \t" and not in_quotes:
            if has_token:
                args.append("".join(cur))
                cur, has_token = [], False
        else:
            cur.append(ch)
            has_token = True
        i += 1
    if has_token:
        args.append("".join(cur))
    return args


_SET_RE = re.compile(r'^\s*set\s+"([^=]+)=(.*)"\s*$', re.IGNORECASE)


def parse_strategy(bat_text: str, variables: Dict[str, str]) -> List[str]:
    """Возвращает аргументы winws.exe из .bat-стратегии."""
    env = dict(variables)
    for line in _join_continuations(bat_text):
        m = _SET_RE.match(line)
        if m and "winws.exe" not in line.lower():
            env[m.group(1).strip()] = expand_vars(m.group(2), env)
            continue
        low = line.lower()
        pos = low.find("winws.exe")
        if pos == -1 or line.lstrip().startswith("::") or low.lstrip().startswith("rem "):
            continue
        rest = line[pos + len("winws.exe"):]
        if rest.startswith('"'):
            rest = rest[1:]
        missing: List[str] = []
        expanded = expand_vars(rest, env, missing)
        if missing:
            log.warning("В стратегии не заданы переменные: %s", ", ".join(sorted(set(missing))))
        return split_cmd_args(expanded)
    raise ValueError("в файле стратегии не найден запуск winws.exe")


def natural_key(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def validate_ports(text: str) -> Optional[str]:
    """Проверка диапазона портов как в service.bat; None — если неверно."""
    value = (text or "").replace(" ", "")
    if not value:
        return None
    for item in value.split(","):
        m = re.fullmatch(r"([1-9]\d{0,4})(?:-([1-9]\d{0,4}))?", item)
        if not m:
            return None
        start = int(m.group(1))
        end = int(m.group(2) or m.group(1))
        if start > 65535 or end > 65535 or start > end:
            return None
    return value


# ======================================================================

@dataclass
class GameFilter:
    mode: str = "disabled"
    tcp: str = "1024-65535"
    udp: str = "1024-65535"

    def variables(self) -> Dict[str, str]:
        tcp = self.tcp if self.mode in ("all", "tcp") else GAME_FILTER_OFF
        udp = self.udp if self.mode in ("all", "udp") else GAME_FILTER_OFF
        if self.mode == "all" or self.mode == "tcp":
            common = self.tcp
        elif self.mode == "udp":
            common = self.udp
        else:
            common = GAME_FILTER_OFF
        return {"GameFilter": common, "GameFilterTCP": tcp, "GameFilterUDP": udp}


@dataclass
class ZapretStatus:
    installed: bool = False
    version: str = ""
    running: bool = False
    own_process: bool = False
    foreign_processes: List[str] = field(default_factory=list)
    service_state: Optional[str] = None
    service_strategy: str = ""

    @property
    def service_installed(self) -> bool:
        return self.service_state is not None


class ZapretManager:
    def __init__(self, root: Path, winws_log: Path):
        self.root = root
        self.winws_log = winws_log
        self._proc: Optional[subprocess.Popen] = None

    # ---------------------------------------------------------- пути
    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def lists(self) -> Path:
        return self.root / "lists"

    @property
    def utils(self) -> Path:
        return self.root / "utils"

    @property
    def winws(self) -> Path:
        return self.bin / "winws.exe"

    def is_installed(self) -> bool:
        return self.winws.exists()

    def local_version(self) -> str:
        try:
            text = (self.root / "service.bat").read_text(encoding="utf-8", errors="replace")
            m = re.search(r'set\s+"LOCAL_VERSION=([^"]+)"', text)
            if m:
                return m.group(1).strip()
        except OSError:
            pass
        return ""

    # ---------------------------------------------------------- стратегии
    def strategies(self) -> List[str]:
        if not self.root.exists():
            return []
        names = [p.name for p in self.root.glob("*.bat") if not p.name.lower().startswith("service")]
        return sorted(names, key=natural_key)

    def variables(self) -> Dict[str, str]:
        root = str(self.root) + os.sep
        v = {
            "~dp0": root,
            "BIN": str(self.bin) + os.sep,
            "LISTS": str(self.lists) + os.sep,
        }
        v.update(self.load_game_filter().variables())
        return v

    def build_args(self, strategy: str) -> List[str]:
        path = self.root / strategy
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp866", errors="replace")
        return parse_strategy(text, self.variables())

    def command_line(self, strategy: str) -> str:
        return subprocess.list2cmdline([str(self.winws)] + self.build_args(strategy))

    # ---------------------------------------------------------- Game Filter
    @property
    def game_filter_file(self) -> Path:
        return self.utils / "game_filter.enabled"

    def load_game_filter(self) -> GameFilter:
        gf = GameFilter()
        try:
            text = self.game_filter_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return gf
        tcp_c = udp_c = None
        for line in text.splitlines():
            key, _, value = line.strip().partition("=")
            key = key.strip().lower()
            value = value.strip()
            if key == "mode":
                gf.mode = value.lower()
            elif key == "all":
                gf.mode = "all"
            elif key == "udp":
                if value:
                    udp_c = value
                else:
                    gf.mode = "udp"
            elif key == "tcp":
                if value:
                    tcp_c = value
                else:
                    gf.mode = "tcp"
        if gf.mode not in GAME_MODES:
            gf.mode = "disabled"
        gf.tcp = validate_ports(tcp_c or "") or gf.tcp
        gf.udp = validate_ports(udp_c or "") or gf.udp
        return gf

    def save_game_filter(self, gf: GameFilter) -> None:
        self.utils.mkdir(parents=True, exist_ok=True)
        self.game_filter_file.write_text(f"mode={gf.mode}\ntcp={gf.tcp}\nudp={gf.udp}\n",
                                         encoding="utf-8")

    # ---------------------------------------------------------- IPSet
    @property
    def ipset_file(self) -> Path:
        return self.lists / "ipset-all.txt"

    @property
    def ipset_backup(self) -> Path:
        return self.lists / "ipset-all.txt.backup"

    def ipset_status(self) -> str:
        try:
            text = self.ipset_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "any"
        if not text.strip():
            return "any"
        if IPSET_NONE_MARK in text:
            return "none"
        return "loaded"

    def set_ipset_mode(self, mode: str) -> None:
        current = self.ipset_status()
        if mode == current:
            return
        if mode in ("none", "any"):
            if current == "loaded":
                shutil.copyfile(self.ipset_file, self.ipset_backup)
            self.ipset_file.write_text(IPSET_NONE_MARK + "\n" if mode == "none" else "",
                                       encoding="utf-8")
        elif mode == "loaded":
            if not self.ipset_backup.exists():
                raise FileNotFoundError("нет сохранённого списка IP — сначала обновите IPSet-список")
            shutil.copyfile(self.ipset_backup, self.ipset_file)
        else:
            raise ValueError(mode)
        log.info("IPSet Filter: %s → %s", current, mode)

    def update_ipset_list(self) -> int:
        text = github.fetch_text(IPSET_URL, timeout=30)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 10:
            raise IOError("получен подозрительно короткий список IP")
        data = "\n".join(lines) + "\n"
        self.ipset_file.write_text(data, encoding="utf-8")
        self.ipset_backup.write_text(data, encoding="utf-8")
        log.info("IPSet-список обновлён: %d записей", len(lines))
        return len(lines)

    # ---------------------------------------------------------- фейки
    def fake_files(self) -> List[str]:
        if not self.bin.exists():
            return []
        return sorted((p.stem for p in self.bin.glob("*.bin") if not p.stem.upper().startswith("ACTIVE_")),
                      key=natural_key)

    @staticmethod
    def _sha(path: Path) -> Optional[str]:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def active_fakes(self) -> Dict[str, Optional[str]]:
        hashes = {name: self._sha(self.bin / f"{name}.bin") for name in self.fake_files()}
        result: Dict[str, Optional[str]] = {}
        for kind, (fname, _) in FAKE_TARGETS.items():
            cur = self._sha(self.bin / fname)
            result[kind] = next((n for n, h in hashes.items() if cur and h == cur), None)
        return result

    def set_active_fake(self, kind: str, fake_name: str) -> None:
        target = self.bin / FAKE_TARGETS[kind][0]
        source = self.bin / f"{fake_name}.bin"
        if not source.exists():
            raise FileNotFoundError(source.name)
        shutil.copyfile(source, target)
        log.info("Активный фейк %s → %s", FAKE_TARGETS[kind][1], fake_name)

    # ---------------------------------------------------------- пользовательские списки
    def ensure_user_lists(self) -> None:
        if not self.lists.exists():
            return
        for name, (_, default) in USER_LISTS.items():
            p = self.lists / name
            if not p.exists():
                p.write_text(default, encoding="utf-8")

    def read_user_list(self, name: str) -> str:
        try:
            return (self.lists / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return USER_LISTS[name][1]

    def write_user_list(self, name: str, text: str) -> None:
        lines = [ln.strip() for ln in text.splitlines()]
        content = "\n".join(ln for ln in lines if ln)
        if not any(ln and not ln.startswith("#") for ln in lines):
            content = USER_LISTS[name][1].strip()  # пустой список ломает winws
        (self.lists / name).write_text(content + "\n", encoding="utf-8")
        log.info("Сохранён список %s", name)

    # ---------------------------------------------------------- исключения программ
    _APP_LINE = re.compile(r"^#\s*app:\s*(.+?)\s*$")

    def _split_app_block(self, text: str) -> Tuple[str, List[str], List[str]]:
        """(текст без блока, имена программ из блока, свои домены из блока)."""
        names: List[str] = []
        custom: List[str] = []
        outside: List[str] = []
        inside = False
        current = None
        for line in text.splitlines():
            s = line.strip()
            if s == APP_BLOCK_BEGIN:
                inside = True
                continue
            if s == APP_BLOCK_END:
                inside = False
                continue
            if not inside:
                outside.append(line)
                continue
            m = self._APP_LINE.match(s)
            if m:
                current = m.group(1)
                if current != "custom":
                    names.append(current)
            elif s and not s.startswith("#") and current == "custom":
                custom.append(s)
        return "\n".join(outside), names, custom

    def app_exclusions(self) -> Tuple[List[str], List[str]]:
        _, names, custom = self._split_app_block(self.read_user_list("list-exclude-user.txt"))
        return names, custom

    def set_app_exclusions(self, names: List[str], custom_domains: List[str]) -> None:
        rest, _, _ = self._split_app_block(self.read_user_list("list-exclude-user.txt"))
        lines = [ln for ln in rest.splitlines() if ln.strip()]
        block: List[str] = []
        for name in names:
            if name in APP_EXCLUSIONS:
                block.append(f"# app: {name}")
                block.extend(APP_EXCLUSIONS[name])
        domains = [d.strip().lower() for d in custom_domains if d.strip()]
        if domains:
            block.append("# app: custom")
            block.extend(domains)
        if block:
            lines += [APP_BLOCK_BEGIN] + block + [APP_BLOCK_END]
        if not any(ln.strip() and not ln.strip().startswith("#") for ln in lines):
            lines = [USER_LISTS["list-exclude-user.txt"][1].strip()]
        (self.lists / "list-exclude-user.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Исключения программ: %s", ", ".join(names + (["свои домены"] if domains else [])) or "нет")

    # ---------------------------------------------------------- статус
    def status(self) -> ZapretStatus:
        st = ZapretStatus(installed=self.is_installed(), version=self.local_version())
        for p in winutil.find_processes("winws.exe"):
            st.running = True
            if not p.exe or winutil.same_path(p.exe, self.winws):
                st.own_process = True
            else:
                st.foreign_processes.append(p.exe)
        st.service_state = winutil.service_state(SERVICE_NAME)
        if st.service_installed:
            st.service_strategy = self.service_strategy() or ""
        return st

    def service_strategy(self) -> Optional[str]:
        if not winutil.IS_WINDOWS:
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SERVICE_REG_KEY) as k:
                value, _ = winreg.QueryValueEx(k, SERVICE_REG_VALUE)
                return str(value)
        except OSError:
            return None

    # ---------------------------------------------------------- запуск / остановка
    @staticmethod
    def enable_tcp_timestamps() -> None:
        if not winutil.IS_WINDOWS:
            return
        res = winutil.run(["netsh", "interface", "tcp", "show", "global"], encoding="cp437")
        for line in res.stdout.splitlines():
            if "timestamps" in line.lower():
                if "enabled" in line.lower():
                    return
                break
        winutil.run_logged(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"])

    def _check_ready(self, strategy: str) -> None:
        if not self.is_installed():
            raise RuntimeError("zapret ещё не скачан — нажмите «Проверить обновления»")
        if not (self.root / strategy).exists():
            raise FileNotFoundError(f"стратегия {strategy} не найдена")
        self.ensure_user_lists()

    def start_process(self, strategy: str) -> None:
        self._check_ready(strategy)
        args = self.build_args(strategy)
        if winutil.service_state(SERVICE_NAME) is not None:
            log.info("Удаляю службу zapret, чтобы запустить обход вручную")
            winutil.service_stop_delete(SERVICE_NAME)
        self.kill_winws()
        self.enable_tcp_timestamps()
        self.winws_log.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.winws_log, "wb")
        try:
            log.info("Запуск zapret: %s", strategy)
            self._proc = winutil.popen_hidden([str(self.winws)] + args, cwd=self.bin, stdout=logf)
        finally:
            logf.close()
        time.sleep(1.5)
        if self._proc.poll() is not None:
            tail = self._read_log_tail()
            self._proc = None
            raise RuntimeError(f"winws.exe завершился сразу после запуска.\n{tail}")

    def _read_log_tail(self, n: int = 15) -> str:
        try:
            lines = self.winws_log.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def kill_winws(self) -> None:
        procs = winutil.find_processes("winws.exe")
        for p in procs:
            log.info("Останавливаю winws.exe (pid %s)", p.pid)
            winutil.kill_pid_tree(p.pid)
        self._proc = None

    def install_service(self, strategy: str) -> None:
        self._check_ready(strategy)
        cmdline = self.command_line(strategy)
        self.kill_winws()
        winutil.service_stop_delete(SERVICE_NAME)
        self.enable_tcp_timestamps()
        log.info("Установка службы zapret: %s", strategy)
        res = winutil.run(["sc", "create", SERVICE_NAME, "binPath=", cmdline,
                           "DisplayName=", "zapret", "start=", "auto"])
        if res.returncode != 0:
            raise RuntimeError(f"sc create: {(res.stdout + res.stderr).strip()}")
        winutil.run_logged(["sc", "description", SERVICE_NAME, "Zapret DPI bypass software"])
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, SERVICE_REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, SERVICE_REG_VALUE, 0, winreg.REG_SZ, Path(strategy).stem)
        res = winutil.run(["sc", "start", SERVICE_NAME])
        if res.returncode != 0:
            raise RuntimeError(f"служба не запустилась: {(res.stdout + res.stderr).strip()}")

    def remove_services(self) -> None:
        """Аналог «Remove Services» из service.bat."""
        winutil.service_stop_delete(SERVICE_NAME)
        self.kill_winws()
        for name in ("WinDivert", "WinDivert14"):
            winutil.service_stop_delete(name)
        log.info("Службы zapret и WinDivert удалены")

    def start(self, strategy: str, mode: str) -> None:
        if mode == "service":
            self.install_service(strategy)
        else:
            self.start_process(strategy)

    def stop(self) -> None:
        if winutil.service_state(SERVICE_NAME) is not None:
            winutil.service_stop_delete(SERVICE_NAME)
        self.kill_winws()
        log.info("zapret остановлен")

    # ---------------------------------------------------------- hosts
    @staticmethod
    def hosts_path() -> Path:
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"

    HOSTS_BEGIN = "# >>> BypassHub: zapret-discord-youtube hosts >>>"
    HOSTS_END = "# <<< BypassHub: zapret-discord-youtube hosts <<<"

    def hosts_check(self) -> Tuple[bool, str]:
        """(нужно ли обновить, актуальный текст из репозитория)."""
        remote = github.fetch_text(HOSTS_URL)
        lines = [ln.strip() for ln in remote.splitlines() if ln.strip()]
        if not lines:
            raise IOError("получен пустой hosts из репозитория")
        try:
            local = self.hosts_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            local = ""
        needs = lines[0] not in local or lines[-1] not in local
        return needs, "\n".join(lines) + "\n"

    def _strip_block(self, text: str) -> str:
        pattern = re.compile(re.escape(self.HOSTS_BEGIN) + r".*?" + re.escape(self.HOSTS_END) + r"\r?\n?",
                             re.DOTALL)
        return pattern.sub("", text)

    def hosts_apply(self, block: str) -> None:
        path = self.hosts_path()
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        backup = path.with_name("hosts.bypasshub.bak")
        if not backup.exists():
            shutil.copyfile(path, backup)
        text = self._strip_block(current).rstrip("\r\n")
        text += f"\n\n{self.HOSTS_BEGIN}\n{block.strip()}\n{self.HOSTS_END}\n"
        path.write_text(text.lstrip("\n").replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")
        winutil.run_logged(["ipconfig", "/flushdns"])
        log.info("hosts обновлён (резервная копия: %s)", backup)

    def hosts_remove(self) -> bool:
        path = self.hosts_path()
        current = path.read_text(encoding="utf-8", errors="replace")
        stripped = self._strip_block(current)
        if stripped == current:
            return False
        path.write_text(stripped.rstrip("\r\n") + "\r\n", encoding="utf-8")
        winutil.run_logged(["ipconfig", "/flushdns"])
        log.info("Записи BypassHub удалены из hosts")
        return True

    # ---------------------------------------------------------- тесты
    def run_tests(self) -> None:
        script = self.utils / "test zapret.ps1"
        if not script.exists():
            raise FileNotFoundError(script.name)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(self.root), creationflags=winutil.CREATE_NEW_CONSOLE if winutil.IS_WINDOWS else 0,
        )

    # ---------------------------------------------------------- установка / обновление
    def install_release(self, zip_path: Path, strategy: str, mode: str,
                        restore_running: bool = True) -> str:
        """Ставит релиз из zip поверх текущей версии, сохраняя пользовательские настройки.

        Возвращает стратегию, которая используется после обновления."""
        parent = self.root.parent
        staging_tmp = parent / "zapret_extract"
        staging = parent / "zapret_new"
        old = parent / "zapret_old"
        for p in (staging_tmp, staging):
            shutil.rmtree(p, ignore_errors=True)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging_tmp)
        src = _find_zapret_root(staging_tmp)
        if src is None:
            shutil.rmtree(staging_tmp, ignore_errors=True)
            raise RuntimeError("в архиве не найден bin/winws.exe")
        shutil.move(str(src), str(staging))
        shutil.rmtree(staging_tmp, ignore_errors=True)

        new = ZapretManager(staging, self.winws_log)
        was_service = was_process = False
        if self.root.exists():
            st = self.status()
            was_service = st.service_installed
            was_process = st.running and not was_service
            if st.service_strategy and was_service:
                strategy = st.service_strategy + ".bat"
            _migrate_user_data(self, new)

        new.ensure_user_lists()
        (new.utils / "check_updates.enabled").unlink(missing_ok=True)  # обновлениями занимается BypassHub

        if self.root.exists():
            self.remove_services()
            time.sleep(1.0)
            shutil.rmtree(old, ignore_errors=True)
            _retry(lambda: self.root.rename(old), "не удалось переместить старую папку zapret "
                                                    "(закройте окна и программы, использующие её)")
        try:
            _retry(lambda: staging.rename(self.root), "не удалось поставить новую версию")
        except Exception:
            if old.exists() and not self.root.exists():
                old.rename(self.root)
            raise
        shutil.rmtree(old, ignore_errors=True)

        if not (self.root / strategy).exists():
            log.warning("Стратегии %s нет в новой версии — выбрана general.bat", strategy)
            strategy = "general.bat"

        if restore_running and (was_service or was_process):
            self.start(strategy, "service" if was_service else mode)
        return strategy


def _retry(fn, message: str, attempts: int = 6) -> None:
    last: Optional[Exception] = None
    for _ in range(attempts):
        try:
            fn()
            return
        except OSError as exc:
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"{message}: {last}")


def _find_zapret_root(base: Path) -> Optional[Path]:
    if (base / "bin" / "winws.exe").exists():
        return base
    for child in base.iterdir():
        if child.is_dir() and (child / "bin" / "winws.exe").exists():
            return child
    return None


def _migrate_user_data(old: ZapretManager, new: ZapretManager) -> None:
    """Переносит пользовательские настройки из старой версии zapret в новую."""
    new.lists.mkdir(parents=True, exist_ok=True)
    new.utils.mkdir(parents=True, exist_ok=True)
    for name in USER_LISTS:
        src = old.lists / name
        if src.exists():
            shutil.copyfile(src, new.lists / name)
    if old.game_filter_file.exists():
        shutil.copyfile(old.game_filter_file, new.game_filter_file)
    results = old.utils / "test results"
    if results.is_dir():
        shutil.copytree(results, new.utils / "test results", dirs_exist_ok=True)
    # собственные стратегии пользователя
    for bat in old.root.glob("*.bat"):
        if not (new.root / bat.name).exists():
            shutil.copyfile(bat, new.root / bat.name)
            log.info("Перенесена пользовательская стратегия %s", bat.name)
    # режим IPSet
    mode = old.ipset_status()
    try:
        if mode == "loaded" and not new.ipset_backup.exists() and old.ipset_status() == "loaded":
            shutil.copyfile(old.ipset_file, new.ipset_backup)
        new.set_ipset_mode(mode)
    except Exception as exc:
        log.warning("Не удалось восстановить режим IPSet (%s): %r", mode, exc)
    # активные фейки
    for kind, name in old.active_fakes().items():
        if name and (new.bin / f"{name}.bin").exists():
            try:
                new.set_active_fake(kind, name)
            except Exception as exc:
                log.warning("Не удалось восстановить фейк %s: %r", kind, exc)
