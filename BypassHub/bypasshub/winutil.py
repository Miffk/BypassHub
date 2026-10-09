"""Обёртки над Windows: права администратора, процессы, службы, автозапуск.

Модуль импортируется и на других ОС (для тестов), но реально работает только
на Windows.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .log import log

IS_WINDOWS = sys.platform == "win32"
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
        encoding: str = "cp866") -> subprocess.CompletedProcess:
    """Запуск консольной команды без окна; вывод возвращается строкой."""
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
                    result.append(ProcInfo(p.info["pid"], p.info["name"], p.info.get("exe") or ""))
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
                result.append(ProcInfo(p.info["pid"], p.info["name"], p.info.get("exe") or ""))
        except Exception:
            continue
    return result


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


def clean_env() -> dict:
    """Окружение для запуска других программ из собранного exe.

    PyInstaller передаёт дочерним процессам служебные переменные (_PYI_*,
    _MEIPASS2) и свою временную папку в PATH. Если запустить exe с тем же путём
    (например, обновлённый BypassHub.exe), он решит, что уже распакован, и
    возьмёт файлы из временной папки старого процесса, которая исчезает."""
    env = os.environ.copy()
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
    for line in res.stdout.splitlines():
        if "STATE" in line.upper() and ":" in line:
            parts = line.split(":", 1)[1].split()
            if len(parts) >= 2:
                return parts[1].upper()
    return "UNKNOWN"


def service_stop_delete(name: str) -> None:
    if service_state(name) is None:
        return
    run_logged(["net", "stop", name], timeout=30)
    run_logged(["sc", "delete", name])


def list_services() -> str:
    """Сырой вывод `sc query` по всем службам (для диагностики)."""
    if not IS_WINDOWS:
        return ""
    return run(["sc", "query"], encoding="cp437", timeout=30).stdout


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
    if not IS_WINDOWS:
        return False
    return run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0


def set_autostart(enabled: bool) -> None:
    """Автозапуск через Планировщик заданий — так программа стартует с правами
    администратора без запроса UAC при каждом входе в систему."""
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


def acquire_single_instance(name: str = "Global\\BypassHub_SingleInstance") -> bool:
    global _mutex_handle
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
    else:
        print(f"[{title}] {text}", file=sys.stderr)


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
