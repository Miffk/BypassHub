"""Обёртки над ОС: права администратора, процессы, службы, автозапуск.

Сделано для Windows; на Linux часть функций передаёт работу модулю linux
(автозапуск, брандмауэр, один экземпляр), остальное там не нужно.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .log import log

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NEW_CONSOLE = 0x00000010

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def is_admin() -> bool:
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: Sequence[str] = ()) -> bool:
    """Перезапускает текущую программу с запросом UAC. True — если запуск начат."""
    if not IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, list(sys.argv[1:])
    else:
        exe, args = sys.executable, [os.path.abspath(sys.argv[0])] + list(sys.argv[1:])
    args += list(extra_args)
    params = subprocess.list2cmdline(args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def run(cmd: Sequence[str], timeout: float = 60, check: bool = False,
        encoding: Optional[str] = None) -> subprocess.CompletedProcess:
    """Запуск консольной команды без окна; вывод возвращается строкой."""
    encoding = encoding or ("cp866" if IS_WINDOWS else "utf-8")
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    proc = subprocess.run(
        list(cmd), capture_output=True, timeout=timeout, **kwargs,
    )
    out = proc.stdout.decode(encoding, errors="replace")
    err = proc.stderr.decode(encoding, errors="replace")
    result = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} → код {proc.returncode}: {(out + err).strip()}")
    return result


def run_logged(cmd: Sequence[str], timeout: float = 60) -> int:
    res = run(cmd, timeout=timeout)
    text = (res.stdout + res.stderr).strip()
    log.debug("$ %s → %s%s", subprocess.list2cmdline(cmd), res.returncode,
              ("\n" + text) if text else "")
    return res.returncode


# ---------------------------------------------------------------- процессы

@dataclass
class ProcInfo:
    pid: int
    name: str
    exe: str


def find_processes(name: str) -> List[ProcInfo]:
    """Процессы с данным именем exe (без учёта регистра)."""
    name_l = name.lower()
    result: List[ProcInfo] = []
    if psutil is not None:
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                pname = (p.info.get("name") or "").lower()
                if pname == name_l:
                    result.append(ProcInfo(p.info["pid"], p.info["name"], _exe_of(p)))
            except Exception:
                continue
        return result
    if IS_WINDOWS:  # запасной вариант без psutil
        res = run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {name}"])
        for line in res.stdout.splitlines():
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) > 1 and parts[0].lower() == name_l:
                try:
                    result.append(ProcInfo(int(parts[1]), parts[0], ""))
                except ValueError:
                    pass
    return result


def find_processes_like(substring: str) -> List[ProcInfo]:
    """Процессы, в имени exe которых есть подстрока (без учёта регистра)."""
    sub = substring.lower()
    result: List[ProcInfo] = []
    if psutil is None:
        return result
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if sub in (p.info.get("name") or "").lower():
                result.append(ProcInfo(p.info["pid"], p.info["name"], _exe_of(p)))
        except Exception:
            continue
    return result


def _exe_of(p) -> str:
    """Путь к программе процесса. В Linux путь чужого (root) процесса закрыт — тогда берём
    первый аргумент командной строки: её видят все, а nfqws и sing-box запускаются по полному пути."""
    exe = p.info.get("exe") or ""
    if not exe and IS_LINUX:
        try:
            cmd = p.cmdline()
            if cmd and os.path.isabs(cmd[0]):
                exe = cmd[0]
        except Exception:
            pass
    return exe


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    try:
        with socket.create_connection((host if host != "0.0.0.0" else "127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except Exception:
        return False


def kill_pid_tree(pid: int) -> None:
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            if IS_LINUX and parent.uids().real != os.getuid() and os.geteuid() != 0:
                from . import linux  # процесс запущен помощником от root — его и просим
                linux.helper().call("kill", pid=pid)
                return
            procs = parent.children(recursive=True) + [parent]
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
            psutil.wait_procs(procs, timeout=5)
            return
        except psutil.NoSuchProcess:
            return
        except Exception as exc:
            log.debug("psutil kill %s: %r", pid, exc)
    if IS_WINDOWS:
        run(["taskkill", "/F", "/T", "/PID", str(pid)])


def kill_by_name(name: str) -> None:
    for p in find_processes(name):
        kill_pid_tree(p.pid)


def restore_library_path(env: Optional[dict] = None) -> dict:
    """Собранная программа в Linux: загрузчик PyInstaller дописывает в LD_LIBRARY_PATH папку своих
    библиотек (Qt, GLib…), а прежнее значение хранит в LD_LIBRARY_PATH_ORIG. Запущенные нами программы
    (TgWsProxy, kwriteconfig6, xdg-open…) не должны получать чужие библиотеки — возвращаем как было.
    Сам процесс это не затрагивает: пути к библиотекам загрузчик читает только при старте."""
    env = os.environ if env is None else env
    if IS_LINUX and getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:  # и прочие пути внутрь распакованной сборки (QT_PLUGIN_PATH и т. п.)
            for key in [k for k, v in env.items() if meipass in v and k != "PATH"]:
                del env[key]
    return env


def clean_env() -> dict:
    """Окружение для запуска других программ из собранного exe.

    PyInstaller передаёт дочерним процессам служебные переменные (_PYI_*,
    _MEIPASS2) и свою временную папку в PATH. Если запустить exe с тем же путём
    (например, обновлённый BypassHub.exe), он решит, что уже распакован, и
    возьмёт файлы из временной папки старого процесса, которая исчезает."""
    env = restore_library_path(os.environ.copy())
    for key in list(env):
        if key.startswith("_PYI_") or key.startswith("_MEIPASS"):
            del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # PyInstaller 6.9+: «запускаемся с нуля»
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mei = os.path.normcase(os.path.abspath(meipass))
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and os.path.normcase(os.path.abspath(p)) != mei)
    for key in ("TCL_LIBRARY", "TK_LIBRARY"):  # указывают внутрь временной папки
        if meipass and env.get(key, "").startswith(meipass):
            del env[key]
    return env


def popen_hidden(cmd: Sequence[str], cwd: Optional[Path] = None, stdout=None,
                 detached: bool = False) -> subprocess.Popen:
    flags = 0
    if IS_WINDOWS:
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        if detached:
            flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        list(cmd), cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if stdout is not None else subprocess.DEVNULL,
        creationflags=flags, close_fds=True, env=clean_env(),
    )


def open_path(path: str | Path) -> None:
    if IS_WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_url(url: str) -> bool:
    try:
        if IS_WINDOWS:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        import webbrowser
        return webbrowser.open(url)
    except Exception as exc:
        log.warning("Не удалось открыть %s: %r", url, exc)
        return False


# ---------------------------------------------------------------- службы

def service_state(name: str) -> Optional[str]:
    """RUNNING / STOPPED / START_PENDING / STOP_PENDING / ...; None — службы нет."""
    if not IS_WINDOWS:
        return None
    res = run(["sc", "query", name], encoding="cp437")
    if res.returncode != 0:
        return None
    return parse_service_state(res.stdout)


_SERVICE_STATES = ("STOPPED", "START_PENDING", "STOP_PENDING", "RUNNING", "CONTINUE_PENDING",
                   "PAUSE_PENDING", "PAUSED")


def parse_service_state(sc_output: str) -> str:
    """Состояние из вывода `sc query`. Название строки переводится («STATE», «Состояние»…),
    а код и имя состояния («4  RUNNING») — нет, поэтому ищем именно их."""
    m = re.search(r":\s*[1-7]\s+(" + "|".join(_SERVICE_STATES) + r")\b", sc_output)
    return m.group(1) if m else "UNKNOWN"


def parse_service_pid(sc_output: str) -> int:
    """PID процесса службы из вывода `sc queryex` (0 — не запущена)."""
    m = re.search(r"\bPID\s*:\s*(\d+)", sc_output)
    return int(m.group(1)) if m else 0


def service_stop_delete(name: str) -> None:
    if service_state(name) is None:
        return
    run_logged(["net", "stop", name], timeout=30)
    if service_state(name) not in (None, "STOPPED"):
        # служба зависла (например, в START_PENDING) и не останавливается — завершаем её процесс,
        # иначе она может «дозапуститься» позже, уже без нас
        pid = parse_service_pid(run(["sc", "queryex", name], encoding="cp437").stdout)
        if pid:
            run_logged(["taskkill", "/F", "/PID", str(pid)], timeout=15)
    run_logged(["sc", "delete", name])


def list_services() -> str:
    """Сырой вывод `sc query` по всем службам (для диагностики)."""
    if not IS_WINDOWS:
        return ""
    return run(["sc", "query"], encoding="cp437", timeout=30).stdout


# ---------------------------------------------------------------- системный прокси

_INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_BYPASS = ("localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;172.22.*;"
                "172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;"
                "192.168.*;<local>")


def get_system_proxy() -> dict:
    """Текущие настройки прокси Windows (как в «Параметры → Сеть → Прокси»)."""
    if not IS_WINDOWS:
        return {}
    import winreg
    result = {"enable": False, "server": "", "override": ""}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_KEY) as k:
        for name, key in (("enable", "ProxyEnable"), ("server", "ProxyServer"), ("override", "ProxyOverride")):
            try:
                value = winreg.QueryValueEx(k, key)[0]
                result[name] = bool(value) if name == "enable" else str(value)
            except OSError:
                pass
    return result


def set_system_proxy(enable: bool, server: str = "", override: str = "") -> None:
    if not IS_WINDOWS:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if server:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        else:
            try:
                winreg.DeleteValue(k, "ProxyServer")
            except OSError:
                pass
        if override:
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, override)
    try:  # сообщить программам, что настройки изменились
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(None, 39, None, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
        wininet.InternetSetOptionW(None, 37, None, 0)  # INTERNET_OPTION_REFRESH
    except Exception as exc:
        log.debug("InternetSetOption: %r", exc)


# ---------------------------------------------------------------- брандмауэр

def firewall_rule_exists(name: str) -> bool:
    if not IS_WINDOWS:
        return False
    res = run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"], encoding="cp437")
    return res.returncode == 0 and "no rules match" not in res.stdout.lower()


def firewall_allow_tcp(name: str, port: int) -> None:
    """Разрешить входящие TCP-подключения на порт (для всех профилей сети)."""
    if IS_LINUX:
        from . import linux
        linux.firewall_allow_tcp(name, port)
        return
    if not IS_WINDOWS:
        return
    firewall_remove(name)
    res = run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in", "action=allow",
               "protocol=TCP", f"localport={int(port)}", "profile=any"], encoding="cp437")
    if res.returncode != 0:
        raise RuntimeError(f"не удалось добавить правило брандмауэра: {(res.stdout + res.stderr).strip()}")


def firewall_remove(name: str) -> None:
    if IS_LINUX:
        from . import linux
        linux.firewall_remove(name)
    if IS_WINDOWS:
        run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"], encoding="cp437")


# ---------------------------------------------------------------- автозапуск

TASK_NAME = "BypassHub"

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>BypassHub autostart</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>PT10S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{command}</Command><Arguments>{arguments}</Arguments></Exec>
  </Actions>
</Task>
"""


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _self_command() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, "--minimized"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    return exe, subprocess.list2cmdline([os.path.abspath(sys.argv[0]), "--minimized"])


def autostart_enabled() -> bool:
    if IS_LINUX:
        from . import linux
        return linux.autostart_enabled()
    if not IS_WINDOWS:
        return False
    return run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0


def set_autostart(enabled: bool) -> None:
    """Автозапуск через Планировщик заданий — так программа стартует с правами
    администратора без запроса UAC при каждом входе в систему.
    В Linux — ярлык в ~/.config/autostart."""
    if IS_LINUX:
        from . import linux
        linux.set_autostart(enabled)
        return
    if not IS_WINDOWS:
        return
    if not enabled:
        run_logged(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        return
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    command, arguments = _self_command()
    xml = _TASK_XML.format(user=_xml_escape(user), command=_xml_escape(command),
                           arguments=_xml_escape(arguments))
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        res = run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", tmp, "/F"])
        if res.returncode != 0:
            raise RuntimeError((res.stdout + res.stderr).strip() or "schtasks error")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- единственный экземпляр

_mutex_handle = None


def acquire_single_instance(name: str = "Global\\BypassHub_SingleInstance",
                            lock_file: Optional[Path] = None) -> bool:
    global _mutex_handle
    if IS_LINUX and lock_file is not None:
        from . import linux
        return linux.acquire_single_instance(lock_file)
    if not IS_WINDOWS:
        return True
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    handle = k32.CreateMutexW(None, True, name)
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if handle:
            k32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _mutex_handle = handle
    return True


def message_box(text: str, title: str = "BypassHub", error: bool = False) -> None:
    if IS_WINDOWS:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
        return
    print(f"[{title}] {text}", file=sys.stderr)
    import shutil
    for tool, args in (("kdialog", ["--title", title, "--error" if error else "--msgbox", text]),
                       ("zenity", ["--error" if error else "--info", "--title", title, "--text", text]),
                       ("notify-send", [title, text])):
        exe = shutil.which(tool)
        if exe:
            try:
                subprocess.run([exe] + args, timeout=600)
                return
            except Exception:
                continue


def windows_major_version() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return sys.getwindowsversion().major  # type: ignore[attr-defined]
    except Exception:
        return 10


def machine_arch() -> str:
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    if not arch:
        import platform
        arch = platform.machine().upper()
    return arch
