"""Linux (делалось под CachyOS с KDE Plasma): права root, автозапуск, прокси, брандмауэр, systemd.

Окно BypassHub работает от имени пользователя. Всё, что требует root (перехват пакетов
для nfqws, VPN на весь трафик, hosts, службы systemd), выполняет помощник: та же
программа, запущенная через pkexec с ключом --root-helper. Он поднимается при первом
таком действии (система один раз спрашивает пароль), получает команды по каналу
stdin/stdout и завершается вместе с окном. Помощник выполняет только свои команды
(см. HANDLERS) и запускает только программы из папки данных BypassHub.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .log import log

IS_LINUX = sys.platform.startswith("linux")

ETC_DIR = Path("/etc/bypasshub")
LIB_DIR = Path("/usr/local/lib/bypasshub")   # копии программ для служб systemd (принадлежат root)
RUN_DIR = Path("/run/bypasshub")
UNIT_DIR = Path("/etc/systemd/system")
HOSTS = Path("/etc/hosts")

# Программы, которые помощник запускает по просьбе окна
RUN_ALLOWED = ("nft", "iptables", "ip6tables", "systemctl", "modprobe", "resolvectl", "ufw", "firewall-cmd")


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def which(name: str) -> Optional[str]:
    """Как shutil.which, но ищет и в /usr/sbin, /sbin: у обычного пользователя их может не быть в PATH."""
    path = os.pathsep.join([os.environ.get("PATH", ""), "/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                            "/usr/bin", "/sbin", "/bin"])
    return shutil.which(name, path=path)


def machine() -> str:
    """amd64 | arm64 | 386 | arm — как в именах файлов релизов."""
    import platform
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    if m in ("i386", "i486", "i586", "i686", "x86"):
        return "386"
    if m.startswith("arm"):
        return "arm"
    return m


# ====================================================================== один экземпляр

_instance_lock = None


def acquire_single_instance(lock_path: Path) -> bool:
    global _instance_lock
    import fcntl
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _instance_lock = f  # держим открытым до выхода
    return True


# ====================================================================== запуск самой программы

def self_command(extra: Sequence[str] = ()) -> List[str]:
    """Команда запуска BypassHub: собранный файл или python + main.py."""
    if getattr(sys, "frozen", False):
        # у onefile-сборки sys.executable — сам файл программы (а не распакованная копия)
        return [os.path.abspath(sys.executable)] + list(extra)
    return [os.path.abspath(sys.executable), os.path.abspath(sys.argv[0])] + list(extra)


def desktop_entry(command: Sequence[str], icon: str, autostart: bool = False) -> str:
    """Ярлык .desktop (меню приложений или автозапуск)."""
    exec_line = " ".join(_desktop_quote(a) for a in command)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=BypassHub",
        "GenericName=Обход блокировок",
        "Comment=Zapret, TG WS Proxy и VPN в одной программе",
        f"Exec={exec_line}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Network;",
        "StartupWMClass=BypassHub",
    ]
    if autostart:
        lines += ["X-GNOME-Autostart-enabled=true", "X-KDE-autostart-after=panel"]
    return "\n".join(lines) + "\n"


def _desktop_quote(arg: str) -> str:
    """Экранирование аргумента для строки Exec= (спецификация Desktop Entry)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`='):
        return arg.replace("%", "%%")
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") \
        .replace("%", "%%") + '"'


def autostart_file() -> Path:
    return config_home() / "autostart" / "bypasshub.desktop"


def autostart_enabled() -> bool:
    return autostart_file().exists()


def set_autostart(enabled: bool) -> None:
    path = autostart_file()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(self_command(["--minimized"]), icon_name(), autostart=True), encoding="utf-8")


def icon_name() -> str:
    return "bypasshub"


def install_menu_entry(icon_png: Optional[bytes] = None) -> None:
    """Ярлык в меню приложений (и значок) — чтобы программу было легко найти и закрепить.
    Обновляется при каждом запуске: путь к программе мог измениться."""
    apps = data_home() / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / "bypasshub.desktop"
    text = desktop_entry(self_command(), icon_name())
    try:
        if not entry.exists() or entry.read_text(encoding="utf-8") != text:
            entry.write_text(text, encoding="utf-8")
        if icon_png:
            icon = data_home() / "icons" / "hicolor" / "256x256" / "apps" / "bypasshub.png"
            if not icon.exists() or icon.read_bytes() != icon_png:
                icon.parent.mkdir(parents=True, exist_ok=True)
                icon.write_bytes(icon_png)
        if autostart_enabled():  # путь к программе в автозапуске — тоже свежий
            set_autostart(True)
    except OSError as exc:
        log.debug("Ярлык в меню: %r", exc)


# ====================================================================== системный прокси

NO_PROXY = ["localhost", "127.0.0.0/8", "::1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
_KDE_KEYS = ("ProxyType", "httpProxy", "httpsProxy", "ftpProxy", "socksProxy", "NoProxyFor")
_GNOME = "org.gnome.system.proxy"


def _kde_tools() -> Tuple[Optional[str], Optional[str]]:
    for ver in ("6", "5"):
        r, w = which(f"kreadconfig{ver}"), which(f"kwriteconfig{ver}")
        if r and w:
            return r, w
    return None, None


def _run_quiet(cmd: List[str], timeout: float = 10) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("%s: %r", cmd[0], exc)
        return -1, ""


def _has_gnome_proxy() -> bool:
    gs = which("gsettings")
    return bool(gs) and _run_quiet([gs, "get", _GNOME, "mode"])[0] == 0


def get_system_proxy() -> Dict[str, Any]:
    """Текущие настройки прокси рабочего стола (KDE и/или GNOME) — чтобы потом вернуть их."""
    snap: Dict[str, Any] = {}
    r, _ = _kde_tools()
    if r:
        snap["kde"] = {k: _run_quiet([r, "--file", "kioslaverc", "--group", "Proxy Settings", "--key", k])[1]
                       for k in _KDE_KEYS}
    if _has_gnome_proxy():
        gs = which("gsettings")
        g: Dict[str, str] = {"mode": _run_quiet([gs, "get", _GNOME, "mode"])[1],
                             "ignore-hosts": _run_quiet([gs, "get", _GNOME, "ignore-hosts"])[1]}
        for scheme in ("http", "https", "socks"):
            for key in ("host", "port"):
                g[f"{scheme}.{key}"] = _run_quiet([gs, "get", f"{_GNOME}.{scheme}", key])[1]
        snap["gnome"] = g
    return snap


def proxy_commands(port: int, snapshot: Optional[Dict[str, Any]] = None,
                   kde: Optional[Tuple[str, str]] = None, gsettings: Optional[str] = None) -> List[List[str]]:
    """Команды, которые включают прокси 127.0.0.1:port (snapshot=None) или возвращают прежние настройки."""
    cmds: List[List[str]] = []
    if kde:
        w = kde[1]
        if snapshot is None:
            values = {"ProxyType": "1", "httpProxy": f"http://127.0.0.1 {port}",
                      "httpsProxy": f"http://127.0.0.1 {port}", "ftpProxy": "",
                      "socksProxy": f"socks://127.0.0.1 {port}", "NoProxyFor": ",".join(NO_PROXY)}
        else:
            values = dict(snapshot.get("kde") or {})
        for key in _KDE_KEYS:
            if key not in values:
                continue
            base = [w, "--file", "kioslaverc", "--group", "Proxy Settings", "--key", key]
            cmds.append(base + [values[key]] if values[key] else base + ["--delete"])
        dbus = which("dbus-send")
        if dbus:  # сообщить программам KDE, что настройки изменились
            cmds.append([dbus, "--type=signal", "/KIO/Scheduler",
                         "org.kde.KIO.Scheduler.reparseSlaveConfiguration", "string:"])
    if gsettings:
        if snapshot is None:
            g = {"mode": "'manual'", "ignore-hosts": "[" + ", ".join(f"'{h}'" for h in NO_PROXY) + "]"}
            for scheme in ("http", "https", "socks"):
                g[f"{scheme}.host"] = "'127.0.0.1'"
                g[f"{scheme}.port"] = str(port)
        else:
            g = dict(snapshot.get("gnome") or {})
        for key, value in g.items():
            if not value:
                continue
            schema, _, name = key.rpartition(".")
            cmds.append([gsettings, "set", f"{_GNOME}.{schema}" if schema else _GNOME, name, value])
    return cmds


def set_system_proxy(port: int, snapshot: Optional[Dict[str, Any]] = None) -> bool:
    """Включить прокси (snapshot=None) или вернуть сохранённые настройки. False — не нашлось,
    где менять (окружение рабочего стола без настроек прокси)."""
    kde = _kde_tools()
    kde = kde if kde[0] else None
    if snapshot is not None:
        if "kde" not in snapshot:
            kde = None
        gs = which("gsettings") if "gnome" in snapshot else None
    else:
        gs = which("gsettings") if _has_gnome_proxy() else None
    cmds = proxy_commands(port, snapshot, kde, gs)
    for cmd in cmds:
        rc, _ = _run_quiet(cmd)
        if rc != 0:
            log.debug("прокси: %s → %s", " ".join(cmd), rc)
    return bool(kde or gs)


# ====================================================================== zapret: перехват пакетов

QNUM = 200            # очередь NFQUEUE, из которой читает nfqws
DESYNC_MARK = 0x40000000  # этим fwmark nfqws помечает свои пакеты — их не перехватываем повторно
FW_TABLE = "bypasshub"
FW_CHAIN = "BYPASSHUB"


def merge_ports(spec: str) -> List[Tuple[int, int]]:
    """"80,443,1024-65535,50000-50100" → объединённые диапазоны [(80,80), (443,443), (1024,65535)].
    nftables не принимает пересекающиеся диапазоны в одном наборе, поэтому сливаем их сами."""
    ranges: List[Tuple[int, int]] = []
    for item in (spec or "").replace(" ", "").split(","):
        if not item or item.startswith("~"):
            continue
        a, _, b = item.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            continue
        if 1 <= lo <= hi <= 65535:
            ranges.append((lo, hi))
    ranges.sort()
    out: List[Tuple[int, int]] = []
    for lo, hi in ranges:
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _nft_ports(ranges: List[Tuple[int, int]]) -> str:
    return ", ".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in ranges)


def _multiport_chunks(ranges: List[Tuple[int, int]]) -> List[str]:
    """iptables -m multiport принимает до 15 «мест» (диапазон занимает два)."""
    chunks: List[str] = []
    cur: List[str] = []
    used = 0
    for lo, hi in ranges:
        cost = 1 if lo == hi else 2
        if used + cost > 15:
            chunks.append(",".join(cur))
            cur, used = [], 0
        cur.append(str(lo) if lo == hi else f"{lo}:{hi}")
        used += cost
    if cur:
        chunks.append(",".join(cur))
    return chunks


def firewall_script(tcp: str, udp: str, qnum: int = QNUM) -> str:
    """Сценарий sh с командами up/down: пакеты первых секунд соединений на портах стратегии
    уходят в очередь nfqws. Сначала nftables, если его нет — iptables (iptables-nft)."""
    t, u = merge_ports(tcp), merge_ports(udp)
    mark = f"0x{DESYNC_MARK:08x}"
    nft = [f"table inet {FW_TABLE} {{",
           "  chain post {",
           "    type filter hook postrouting priority mangle; policy accept;",
           '    oifname "lo" accept',
           f"    meta mark & {mark} != 0 accept"]
    if t:
        nft.append(f"    tcp dport {{ {_nft_ports(t)} }} ct original packets 1-6 queue num {qnum} bypass")
    if u:
        nft.append(f"    udp dport {{ {_nft_ports(u)} }} ct original packets 1-6 queue num {qnum} bypass")
    nft += ["  }",
            "  chain pre {",
            "    type filter hook prerouting priority filter; policy accept;",
            '    iifname "lo" accept']
    if t:
        nft.append(f"    tcp sport {{ {_nft_ports(t)} }} ct reply packets 1-3 queue num {qnum} bypass")
    nft += ["  }", "}"]

    ipt_up: List[str] = []
    for chain, hook in ((FW_CHAIN + "_POST", "POSTROUTING"), (FW_CHAIN + "_PRE", "PREROUTING")):
        ipt_up += [f'  "$ipt" -t mangle -N {chain} 2>/dev/null; "$ipt" -t mangle -F {chain}',
                   f'  "$ipt" -t mangle -A {chain} -{"o" if hook == "POSTROUTING" else "i"} lo -j RETURN']
        if hook == "POSTROUTING":
            ipt_up.append(f'  "$ipt" -t mangle -A {chain} -m mark --mark {mark}/{mark} -j RETURN')
            protos = (("tcp", t), ("udp", u))
            direction, conn, side = "original", "1:6", "--dports"
        else:
            protos = (("tcp", t),)
            direction, conn, side = "reply", "1:3", "--sports"
        for proto, ranges in protos:
            for chunk in _multiport_chunks(ranges):
                ipt_up.append(f'  "$ipt" -t mangle -A {chain} -p {proto} -m multiport {side} {chunk} '
                              f'-m connbytes --connbytes-dir={direction} --connbytes-mode=packets '
                              f'--connbytes {conn} -j NFQUEUE --queue-num {qnum} --queue-bypass')
        ipt_up.append(f'  "$ipt" -t mangle -I {hook} -j {chain}')
    ipt_down = []
    for chain, hook in ((FW_CHAIN + "_POST", "POSTROUTING"), (FW_CHAIN + "_PRE", "PREROUTING")):
        ipt_down += [f'  while "$ipt" -t mangle -D {hook} -j {chain} 2>/dev/null; do :; done',
                     f'  "$ipt" -t mangle -F {chain} 2>/dev/null; "$ipt" -t mangle -X {chain} 2>/dev/null']

    nl = "\n"
    return f"""#!/bin/sh
# BypassHub: пакеты для nfqws (zapret). Создано автоматически — не редактируйте.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

down() {{
  if command -v nft >/dev/null 2>&1; then
    nft delete table inet {FW_TABLE} 2>/dev/null
  fi
  for ipt in iptables ip6tables; do
    command -v "$ipt" >/dev/null 2>&1 || continue
{nl.join(ipt_down)}
  done
  return 0
}}

up() {{
  down
  modprobe -q nfnetlink_queue 2>/dev/null
  if command -v nft >/dev/null 2>&1; then
    nft -f - <<'EOF' && return 0
{nl.join(nft)}
EOF
    echo "nftables не принял правила, пробую iptables" >&2
  fi
  ok=1
  for ipt in iptables ip6tables; do
    command -v "$ipt" >/dev/null 2>&1 || continue
{nl.join(ipt_up)}
    [ "$ipt" = iptables ] && ok=0
  done
  if [ $ok -ne 0 ]; then
    echo "Нет ни nftables, ни iptables: установите пакет nftables (sudo pacman -S nftables)" >&2
    return 1
  fi
}}

case "$1" in
  up) up ;;
  down) down ;;
  *) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
"""


# ====================================================================== systemd

def systemd_quote(arg: str) -> str:
    """Аргумент для ExecStart= (systemd.service: кавычки, %% и $$)."""
    arg = arg.replace("%", "%%").replace("$", "$$")
    if arg and not any(c in arg for c in ' \t\n"\'\\;'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text(description: str, exec_start: Sequence[str], pre: Sequence[str] = (),
              post: Sequence[str] = (), comment: str = "") -> str:
    lines = ["# BypassHub: создано автоматически."]
    if comment:
        lines.append(f"# {comment}")
    lines += ["[Unit]", f"Description={description}",
              "After=network-online.target", "Wants=network-online.target", "",
              "[Service]", "Type=simple"]
    lines += [f"ExecStartPre={c}" for c in pre]
    lines.append("ExecStart=" + " ".join(systemd_quote(a) for a in exec_start))
    lines += [f"ExecStopPost={c}" for c in post]
    lines += ["Restart=on-failure", "RestartSec=3", "", "[Install]", "WantedBy=multi-user.target", ""]
    return "\n".join(lines)


def unit_state(unit: str) -> Optional[str]:
    """Состояние службы как в Windows: RUNNING / START_PENDING / STOPPED; None — службы нет."""
    if not (UNIT_DIR / unit).exists():
        return None
    sc = which("systemctl")
    if not sc:
        return "UNKNOWN"
    state = _run_quiet([sc, "is-active", unit])[1]
    return {"active": "RUNNING", "activating": "START_PENDING", "reloading": "RUNNING",
            "deactivating": "STOP_PENDING"}.get(state, "STOPPED")


# ====================================================================== брандмауэр (доступ с телефона)

def _active(unit: str) -> bool:
    sc = which("systemctl")
    return bool(sc) and _run_quiet([sc, "is-active", unit])[1] == "active"


def firewall_allow_tcp(name: str, port: int) -> None:
    """Открыть входящий TCP-порт, если включён ufw или firewalld (иначе ничего не нужно)."""
    if _active("ufw.service") and which("ufw"):
        firewall_remove(name)
        helper().run(["ufw", "allow", f"{int(port)}/tcp", "comment", name], check=True)
    elif _active("firewalld.service") and which("firewall-cmd"):
        helper().run(["firewall-cmd", f"--add-port={int(port)}/tcp"], check=True)
        _fw_state_save(name, int(port))


def firewall_remove(name: str) -> None:
    if _active("ufw.service") and which("ufw"):
        out = helper().run(["ufw", "status", "numbered"])["out"]
        numbers = parse_ufw_numbered(out, name)
        for n in sorted(numbers, reverse=True):  # с конца: номера сдвигаются после удаления
            helper().run(["ufw", "--force", "delete", str(n)])
    elif _active("firewalld.service") and which("firewall-cmd"):
        port = _fw_state_load().pop(name, None)
        if port:
            helper().run(["firewall-cmd", f"--remove-port={int(port)}/tcp"])
            _fw_state_save(name, None)


def parse_ufw_numbered(text: str, comment: str) -> List[int]:
    """Номера правил ufw с нашим комментарием из вывода `ufw status numbered`."""
    import re
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*\[\s*(\d+)\]", line)
        if m and line.rstrip().endswith("# " + comment):
            out.append(int(m.group(1)))
    return out


def _fw_state_file() -> Path:
    return (_data_root or data_home() / "BypassHub") / "firewalld.json"


def _fw_state_load() -> Dict[str, int]:
    try:
        return json.loads(_fw_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fw_state_save(name: str, port: Optional[int]) -> None:
    data = _fw_state_load()
    if port:
        data[name] = port
    else:
        data.pop(name, None)
    try:
        _fw_state_file().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


# ====================================================================== помощник с правами root

_data_root: Optional[Path] = None


def set_data_root(root: Path) -> None:
    global _data_root
    _data_root = Path(root)


class HelperError(RuntimeError):
    pass


class RootHelper:
    """Окно → помощник: одна JSON-строка на запрос и одна на ответ."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next = 0

    def _start(self) -> None:
        if os.geteuid() == 0:
            return  # уже root (например, запуск через sudo) — команды выполняются здесь же
        pkexec = which("pkexec")
        if not pkexec:
            raise HelperError("Не найден pkexec (polkit) — он нужен, чтобы запросить права администратора. "
                              "Установите пакет polkit.")
        root = str(_data_root or data_home() / "BypassHub")
        cmd = [pkexec] + self_command(["--root-helper", root])
        log.info("Запрос прав администратора (pkexec)")
        from .winutil import clean_env
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=clean_env(), close_fds=True)
        try:
            hello = self._read()
        except HelperError:
            err = b""
            try:
                err = self._proc.stderr.read() or b""
            except Exception:
                pass
            rc = self._proc.wait(5)
            self._proc = None
            if rc in (126, 127):
                raise HelperError("Нужны права администратора: запрос пароля отменён или пароль неверный.") \
                    from None
            raise HelperError("Не удалось получить права администратора: "
                              + (err.decode("utf-8", "replace").strip() or f"код {rc}")) from None
        if hello.get("result") != "ready":
            raise HelperError(f"помощник ответил неожиданно: {hello}")
        threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True, name="root-helper-err").start()

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                log.debug("root-helper: %s", text)

    def _read(self) -> Dict[str, Any]:
        line = self._proc.stdout.readline() if self._proc else b""
        if not line:
            raise HelperError("помощник с правами администратора завершился")
        return json.loads(line.decode("utf-8"))

    def running(self) -> bool:
        return os.geteuid() == 0 or (self._proc is not None and self._proc.poll() is None)

    def call(self, cmd: str, **args: Any) -> Any:
        with self._lock:
            if os.geteuid() == 0:
                return dispatch(cmd, args, uid=int(os.environ.get("SUDO_UID") or 0),
                                gid=int(os.environ.get("SUDO_GID") or 0))
            if self._proc is None or self._proc.poll() is not None:
                self._proc = None
                self._start()
            self._next += 1
            req = {"id": self._next, "cmd": cmd, "args": args}
            try:
                self._proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
                resp = self._read()
            except (OSError, HelperError, ValueError) as exc:
                self._proc = None
                raise HelperError(f"помощник с правами администратора недоступен: {exc}") from None
            if not resp.get("ok"):
                raise HelperError(resp.get("error") or "ошибка помощника")
            return resp.get("result")

    def run(self, argv: Sequence[str], timeout: float = 60, check: bool = False,
            input: str = "") -> Dict[str, Any]:
        res = self.call("run", argv=list(argv), timeout=timeout, input=input)
        if check and res["rc"] != 0:
            raise HelperError(f"{argv[0]}: {res['out'].strip() or 'код ' + str(res['rc'])}")
        return res

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.stdin.close()
                    self._proc.wait(3)
                except Exception:
                    pass
                self._proc = None


_helper: Optional[RootHelper] = None


def helper() -> RootHelper:
    global _helper
    if _helper is None:
        _helper = RootHelper()
    return _helper


# ---------------------------------------------------------------------- сторона помощника (root)

_children: Dict[int, subprocess.Popen] = {}


def _allowed_exe(path: str) -> bool:
    """Запускать и останавливать можно только программы BypassHub: из папки данных или из LIB_DIR."""
    try:
        real = Path(path).resolve()
    except OSError:
        return False
    roots = [LIB_DIR.resolve()]
    if _data_root is not None:
        roots.append(Path(_data_root).resolve())
    return real.is_file() and any(real == r or r in real.parents for r in roots)


def _allowed_file(path: str) -> bool:
    p = Path(os.path.abspath(path))
    if p == HOSTS:
        return True
    if p.parent == UNIT_DIR and p.name.startswith("bypasshub-") and p.suffix == ".service":
        return True
    return any(d in p.parents for d in (ETC_DIR, LIB_DIR, RUN_DIR))


def _h_run(args: Dict[str, Any], uid: int, gid: int) -> Dict[str, Any]:
    argv = [str(a) for a in args["argv"]]
    if not argv or os.path.basename(argv[0]) not in RUN_ALLOWED:
        raise PermissionError(f"команда не разрешена: {argv[:1]}")
    exe = which(argv[0])
    if not exe:
        raise FileNotFoundError(f"не найдена программа {argv[0]}")
    p = subprocess.run([exe] + argv[1:], input=(args.get("input") or "").encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=float(args.get("timeout") or 60))
    return {"rc": p.returncode, "out": p.stdout.decode("utf-8", errors="replace")}


def _h_spawn(args: Dict[str, Any], uid: int, gid: int) -> Dict[str, Any]:
    argv = [str(a) for a in args["argv"]]
    if not argv or not _allowed_exe(argv[0]):
        raise PermissionError(f"запуск не разрешён: {argv[:1]}")
    logf = subprocess.DEVNULL
    if args.get("log"):
        logf = open(args["log"], "wb")
        try:
            os.fchown(logf.fileno(), uid, gid)  # журнал остаётся файлом пользователя
        except OSError:
            pass
    try:
        proc = subprocess.Popen(argv, cwd=args.get("cwd") or None, stdin=subprocess.DEVNULL, stdout=logf,
                                stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    finally:
        if logf is not subprocess.DEVNULL:
            logf.close()
    _children[proc.pid] = proc
    threading.Thread(target=proc.wait, daemon=True).start()  # не оставлять зомби
    return {"pid": proc.pid}


def _h_poll(args: Dict[str, Any], uid: int, gid: int) -> Optional[int]:
    proc = _children.get(int(args["pid"]))
    return None if proc is None else proc.poll()


def _h_kill(args: Dict[str, Any], uid: int, gid: int) -> bool:
    import psutil
    try:
        p = psutil.Process(int(args["pid"]))
        exe = p.exe() or (p.cmdline() or [""])[0]
    except psutil.NoSuchProcess:
        return True
    if not _allowed_exe(exe):
        raise PermissionError(f"остановка не разрешена: {exe}")
    procs = p.children(recursive=True) + [p]
    for q in procs:
        try:
            q.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for q in alive:
        try:
            q.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=3)
    return True


def _h_write_file(args: Dict[str, Any], uid: int, gid: int) -> bool:
    path = str(args["path"])
    if not _allowed_file(path):
        raise PermissionError(f"запись не разрешена: {path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = path + ".bypasshub.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(args["text"]))
    os.chmod(tmp, int(args.get("mode") or 0o644))
    os.replace(tmp, path)
    return True


def _h_remove_file(args: Dict[str, Any], uid: int, gid: int) -> bool:
    path = str(args["path"])
    if not _allowed_file(path) or Path(path) == HOSTS:
        raise PermissionError(f"удаление не разрешено: {path}")
    Path(path).unlink(missing_ok=True)
    return True


def _h_install_bin(args: Dict[str, Any], uid: int, gid: int) -> str:
    """Копия программы из папки данных в LIB_DIR — для служб systemd (файл принадлежит root)."""
    src = str(args["src"])
    if not _allowed_exe(src):
        raise PermissionError(f"копирование не разрешено: {src}")
    dst = LIB_DIR / os.path.basename(str(args.get("name") or src))
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dst)
    return str(dst)


def _h_zapret_fw(args: Dict[str, Any], uid: int, gid: int) -> Dict[str, Any]:
    """Правила перехвата для nfqws в режиме «Программа»: up — включить, down — убрать."""
    action = "up" if args.get("action") == "up" else "down"
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    script = RUN_DIR / "zapret-fw.sh"
    if action == "up":
        script.write_text(firewall_script(str(args.get("tcp") or ""), str(args.get("udp") or "")), encoding="utf-8")
    elif not script.exists():
        script.write_text(firewall_script("", ""), encoding="utf-8")
    p = subprocess.run(["/bin/sh", str(script), action], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=60)
    return {"rc": p.returncode, "out": p.stdout.decode("utf-8", errors="replace")}


def _h_ping(args: Dict[str, Any], uid: int, gid: int) -> str:
    return "pong"


HANDLERS: Dict[str, Callable[[Dict[str, Any], int, int], Any]] = {
    "ping": _h_ping, "run": _h_run, "spawn": _h_spawn, "poll": _h_poll, "kill": _h_kill,
    "write_file": _h_write_file, "remove_file": _h_remove_file, "install_bin": _h_install_bin,
    "zapret_fw": _h_zapret_fw,
}


def dispatch(cmd: str, args: Dict[str, Any], uid: int, gid: int) -> Any:
    fn = HANDLERS.get(cmd)
    if fn is None:
        raise HelperError(f"неизвестная команда помощника: {cmd}")
    return fn(args, uid, gid)


def serve(data_root: str) -> int:
    """Точка входа помощника (запускается через pkexec). Работает, пока открыт канал от окна."""
    if os.geteuid() != 0:
        print("--root-helper запускается только через pkexec", file=sys.stderr)
        return 2
    set_data_root(Path(data_root))
    uid = int(os.environ.get("PKEXEC_UID") or 0)
    try:
        import pwd
        gid = pwd.getpwuid(uid).pw_gid
    except (KeyError, ImportError):
        gid = uid
    # ответы — только в этот канал; случайный print() не должен попасть в протокол
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    sys.stdout = sys.stderr

    def reply(obj: Dict[str, Any]) -> None:
        out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    reply({"id": 0, "ok": True, "result": "ready"})
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        try:
            reply({"id": req.get("id"), "ok": True,
                   "result": dispatch(str(req.get("cmd")), dict(req.get("args") or {}), uid, gid)})
        except Exception as exc:  # ошибка одной команды не роняет помощника
            reply({"id": req.get("id"), "ok": False, "error": str(exc) or exc.__class__.__name__})
    return 0


def shell_join(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
