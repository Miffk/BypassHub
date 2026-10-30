"""Диагностика (перенос «Run Diagnostics» из service.bat)."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import winutil
from .log import log

OK, WARN, ERROR = "ok", "warn", "error"
CONFLICTING_SERVICES = ("GoodbyeDPI", "discordfix_zapret", "winws1", "winws2")


@dataclass
class Check:
    level: str
    text: str
    link: Optional[str] = None


def _proc_running(name: str) -> bool:
    return bool(winutil.find_processes(name))


# Linux: другие обходы DPI, которые тоже перехватывают пакеты (процессы и службы systemd)
LINUX_CONFLICTS = ("nfqws", "tpws", "ciadpi", "byedpi", "spoofdpi", "goodbyedpi", "zapret")


def run_linux_diagnostics(zapret_root: Path) -> List[Check]:
    from . import linux
    from .zapret import ZapretManager
    zm = ZapretManager(zapret_root, zapret_root)
    out: List[Check] = [Check(OK, f"zapret установлен в: {zapret_root}")]
    if zm.winws.exists() and os.access(zm.winws, os.X_OK):
        out.append(Check(OK, f"nfqws найден ({zm.nfqws_version() or 'версия неизвестна'})"))
    else:
        out.append(Check(ERROR, "nfqws не найден — нажмите «Проверить сейчас» во вкладке «Обновления»"))
    if linux.which("nft"):
        out.append(Check(OK, "nftables есть — правила перехвата ставятся через nft"))
    elif linux.which("iptables"):
        out.append(Check(OK, "nftables нет, правила перехвата ставятся через iptables"))
    else:
        out.append(Check(ERROR, "Нет ни nftables, ни iptables. Установите nftables: sudo pacman -S nftables"))
    if Path("/sys/module/nfnetlink_queue").exists():
        out.append(Check(OK, "Модуль ядра nfnetlink_queue загружен"))
    else:
        out.append(Check(OK, "Модуль ядра nfnetlink_queue загрузится при включении обхода"))
    if linux.which("pkexec"):
        out.append(Check(OK, "pkexec (polkit) есть — права администратора запрашиваются им"))
    else:
        out.append(Check(ERROR, "Нет pkexec — установите пакет polkit"))

    foreign = [p.exe or p.name for name in ("nfqws", "tpws", "ciadpi", "byedpi", "spoofdpi")
               for p in winutil.find_processes(name) if not (p.exe and zm._is_own(p.exe))]
    if foreign:
        out.append(Check(ERROR, "Запущен другой обход DPI: " + ", ".join(sorted(set(foreign)))
                     + ". Выключите его — два обхода мешают друг другу"))
    else:
        out.append(Check(OK, "Других обходов DPI не найдено"))
    found = conflicting_services()
    if found:
        out.append(Check(WARN, f"Включены службы других обходов: {', '.join(found)}. Если zapret из BypassHub не "
                               f"работает, выключите их: sudo systemctl disable --now {' '.join(found)}"))

    import psutil
    try:
        names = [n for n, st in psutil.net_if_stats().items() if st.isup]
    except Exception:
        names = []
    vpns = [n for n in names if re.match(r"(wg|awg|tun|tap|ppp|nordlynx|proton|tailscale)", n.lower())
            and not n.lower().startswith("bypasshub") and n != "BypassHub"]
    if vpns:
        out.append(Check(WARN, f"Подключён VPN: {', '.join(vpns)}. Через VPN обход не нужен, а некоторые VPN "
                               "мешают zapret — выключите их, если обход не работает"))
    else:
        out.append(Check(OK, "Сторонних VPN не найдено"))

    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("all_proxy")
    if proxy:
        out.append(Check(WARN, f"В системе задан прокси: {proxy}. Убедитесь, что он рабочий"))
    else:
        out.append(Check(OK, "Системный прокси не задан переменными окружения"))

    out.append(Check(WARN, "Убедитесь, что в браузере включён безопасный DNS (DoH) с провайдером, отличным от "
                           "провайдера по умолчанию, — иначе провайдер может подменять адреса сайтов"))
    try:
        hosts = ZapretManager.hosts_path().read_text(encoding="utf-8", errors="replace").lower()
        if "youtube.com" in hosts or "youtu.be" in hosts:
            out.append(Check(WARN, "В /etc/hosts есть записи youtube.com/youtu.be — они могут мешать YouTube"))
    except OSError:
        pass
    return out


def run_diagnostics(zapret_root: Path) -> List[Check]:
    if winutil.IS_LINUX:
        return run_linux_diagnostics(zapret_root)
    if not winutil.IS_WINDOWS:
        return [Check(WARN, "Диагностика доступна только в Windows и Linux")]
    out: List[Check] = [Check(OK, f"zapret установлен в: {zapret_root}")]
    services = winutil.list_services()
    services_l = services.lower()

    # Base Filtering Engine
    if winutil.service_state("BFE") == "RUNNING":
        out.append(Check(OK, "Служба Base Filtering Engine работает"))
    else:
        out.append(Check(ERROR, "Служба Base Filtering Engine не запущена — она нужна для работы zapret"))

    # системный прокси
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            enabled = winreg.QueryValueEx(k, "ProxyEnable")[0]
            server = winreg.QueryValueEx(k, "ProxyServer")[0] if enabled else ""
        if enabled:
            out.append(Check(WARN, f"Включён системный прокси: {server}. Убедитесь, что он рабочий, "
                                   "или выключите его"))
        else:
            out.append(Check(OK, "Системный прокси не используется"))
    except OSError:
        out.append(Check(OK, "Системный прокси не используется"))

    # TCP timestamps
    res = winutil.run(["netsh", "interface", "tcp", "show", "global"], encoding="cp437")
    ts_line = next((ln for ln in res.stdout.splitlines() if "timestamps" in ln.lower()), "")
    if "enabled" in ts_line.lower():
        out.append(Check(OK, "TCP timestamps включены"))
    else:
        rc = winutil.run_logged(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"])
        out.append(Check(OK if rc == 0 else ERROR,
                         "TCP timestamps были выключены — включены" if rc == 0
                         else "Не удалось включить TCP timestamps"))

    if _proc_running("AdguardSvc.exe"):
        out.append(Check(ERROR, "Найден процесс Adguard — он может мешать работе Discord",
                         "https://github.com/Flowseal/zapret-discord-youtube/issues/417"))
    else:
        out.append(Check(OK, "Adguard не найден"))

    if "killer" in services_l:
        out.append(Check(ERROR, "Найдены службы Killer — они конфликтуют с zapret",
                         "https://github.com/Flowseal/zapret-discord-youtube/issues/2512#issuecomment-2821119513"))
    else:
        out.append(Check(OK, "Службы Killer не найдены"))

    if any("intel" in ln and "connectivity" in ln and "network" in ln for ln in services_l.splitlines()):
        out.append(Check(ERROR, "Найдена Intel Connectivity Network Service — она конфликтует с zapret",
                         "https://github.com/ValdikSS/GoodbyeDPI/issues/541#issuecomment-2661670982"))
    else:
        out.append(Check(OK, "Intel Connectivity Network Service не найдена"))

    if "tracsrvwrapper" in services_l or "epwd" in services_l:
        out.append(Check(ERROR, "Найдены службы Check Point — они конфликтуют с zapret. Попробуйте удалить Check Point"))
    else:
        out.append(Check(OK, "Check Point не найден"))

    if "smartbyte" in services_l:
        out.append(Check(ERROR, "Найдены службы SmartByte — они конфликтуют с zapret. "
                                "Удалите или отключите SmartByte в services.msc"))
    else:
        out.append(Check(OK, "SmartByte не найден"))

    if re.search(r"[а-яА-ЯёЁ]", str(zapret_root)):
        out.append(Check(WARN, "Путь к zapret содержит кириллицу — если обход не работает, "
                               "задайте другую папку данных (переменная BYPASSHUB_DATA)"))
    else:
        out.append(Check(OK, "Путь к zapret без кириллицы"))

    onedrive = os.environ.get("OneDrive")
    if onedrive and str(zapret_root).lower().startswith(onedrive.lower().rstrip("\\") + "\\"):
        out.append(Check(ERROR, "zapret находится в папке OneDrive — обход может не работать"))
    else:
        out.append(Check(OK, "zapret не в OneDrive"))

    if not list((zapret_root / "bin").glob("*.sys")):
        out.append(Check(ERROR, "Файл WinDivert64.sys не найден — возможно, его удалил антивирус. "
                                "Добавьте папку в исключения и переустановите zapret"))

    vpn = []
    for line in services.splitlines():
        if line.upper().startswith("SERVICE_NAME") and "vpn" in line.lower():
            vpn.append(line.split(":", 1)[1].strip())
    if vpn:
        out.append(Check(WARN, f"Найдены VPN-службы: {', '.join(vpn)}. Некоторые VPN конфликтуют с zapret — "
                               "убедитесь, что они выключены"))
    else:
        out.append(Check(OK, "VPN-службы не найдены"))

    if _doh_enabled():
        out.append(Check(OK, "Безопасный DNS (DoH) в Windows настроен"))
    else:
        out.append(Check(WARN, "Убедитесь, что в браузере включён безопасный DNS с провайдером, отличным от "
                               "провайдера по умолчанию. В Windows 11 можно включить DoH в настройках сети"))

    try:
        from .zapret import ZapretManager
        hosts = ZapretManager.hosts_path().read_text(encoding="utf-8", errors="replace").lower()
        if "youtube.com" in hosts or "youtu.be" in hosts:
            out.append(Check(WARN, "В файле hosts есть записи youtube.com/youtu.be — они могут мешать YouTube"))
    except OSError:
        pass

    # WinDivert без winws
    if not _proc_running("winws.exe") and winutil.service_state("WinDivert") in ("RUNNING", "STOP_PENDING"):
        winutil.service_stop_delete("WinDivert")
        if winutil.service_state("WinDivert") is None:
            out.append(Check(OK, "Служба WinDivert работала без winws.exe — удалена"))
        else:
            out.append(Check(ERROR, "WinDivert работает без winws.exe и не удаляется. "
                                    "Проверьте, не использует ли его другой обход (GoodbyeDPI и т.п.)"))

    found = conflicting_services()
    if found:
        out.append(Check(ERROR, f"Найдены конфликтующие службы обхода: {', '.join(found)}. "
                                "Их можно удалить кнопкой ниже"))
    else:
        out.append(Check(OK, "Конфликтующих обходов не найдено"))
    return out


def _doh_enabled() -> bool:
    try:
        import winreg
        base = r"System\CurrentControlSet\Services\Dnscache\InterfaceSpecificParameters"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                iface = winreg.EnumKey(root, i)
                if _doh_in_tree(root, iface):
                    return True
    except OSError:
        pass
    return False


def _doh_in_tree(parent, name: str, depth: int = 0) -> bool:
    import winreg
    try:
        with winreg.OpenKey(parent, name) as key:
            try:
                if int(winreg.QueryValueEx(key, "DohFlags")[0]) > 0:
                    return True
            except OSError:
                pass
            if depth < 4:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    if _doh_in_tree(key, winreg.EnumKey(key, i), depth + 1):
                        return True
    except OSError:
        pass
    return False


def conflicting_services() -> List[str]:
    if winutil.IS_LINUX:
        sc = shutil.which("systemctl")
        if not sc:
            return []
        res = winutil.run([sc, "list-units", "--type=service", "--state=active", "--no-legend", "--plain"],
                          timeout=15)
        units = [ln.split()[0] for ln in res.stdout.splitlines() if ln.split()]
        return [u for u in units if any(c in u.lower() for c in LINUX_CONFLICTS) and not u.startswith("bypasshub")]
    return [s for s in CONFLICTING_SERVICES if winutil.service_state(s) is not None]


def remove_conflicting_services() -> List[str]:
    if winutil.IS_LINUX:  # чужие службы systemd не удаляем — только выключаем
        from . import linux
        found = conflicting_services()
        for unit in found:
            linux.helper().run(["systemctl", "disable", "--now", unit], timeout=60)
        log.info("Выключены службы других обходов: %s", ", ".join(found) or "нет")
        return found
    removed = []
    for s in conflicting_services():
        winutil.service_stop_delete(s)
        removed.append(s)
    for s in ("WinDivert", "WinDivert14"):
        winutil.service_stop_delete(s)
    log.info("Удалены конфликтующие службы: %s", ", ".join(removed) or "нет")
    return removed


DISCORD_VARIANTS = (
    ("Discord.exe", "discord"),
    ("DiscordPTB.exe", "discordptb"),
    ("DiscordCanary.exe", "discordcanary"),
    ("DiscordDevelopment.exe", "discorddevelopment"),
)


def _discord_dirs() -> List[tuple]:
    """(имя процесса, папка данных Discord) для каждой установленной версии."""
    if not winutil.IS_LINUX:
        appdata = Path(os.environ.get("APPDATA", ""))
        return [(exe, appdata / folder) for exe, folder in DISCORD_VARIANTS]
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    flatpak = Path.home() / ".var" / "app" / "com.discordapp.Discord" / "config"
    out = []
    for exe, folder in DISCORD_VARIANTS:
        name = exe[:-4]  # в Linux процесс называется Discord, DiscordPTB…
        out.append((name, config / folder))
        if folder == "discord":
            out.append((name, flatpak / folder))
    return out


def clear_discord_cache() -> List[str]:
    report: List[str] = []
    for exe, base in _discord_dirs():
        if not base.is_dir():
            continue
        winutil.kill_by_name(exe)
        for sub in ("Cache", "Code Cache", "GPUCache"):
            d = base / sub
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                report.append(f"{'не удалось удалить' if d.exists() else 'удалено'}: {d}")
    if not report:
        report.append("Установленный Discord не найден")
    for line in report:
        log.info("Кэш Discord: %s", line)
    return report
