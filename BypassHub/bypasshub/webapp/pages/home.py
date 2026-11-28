"""Главная: состояние для webui/home.js и действия её кнопок (они же работают с любой страницы)."""
from __future__ import annotations

from typing import Any, Dict, List

from ... import __version__, vpn, winutil
from .. import ui

KIND_LABELS = {"vless": "VLESS", "awg": "AmneziaWG"}
AVAIL = (("Discord", "chat"), ("YouTube", "play"), ("Telegram", "send"))


def _country(name: str) -> str:
    """«NL», «Германия» → короткий код для значка сервера."""
    from ...netcheck import COUNTRIES
    n = (name or "").strip()
    if len(n) == 2 and n.isalpha():
        return n.upper()
    for code, title in COUNTRIES.items():
        if title.lower() in n.lower():
            return code
    return (n[:2] or "—").upper()


def vpn_state(app) -> Dict[str, Any]:
    core, vs = app.core, app.vs
    active, auto = core.vpn_active(), core.vpn_is_auto()
    busy = app.busy.get("vpn")
    if busy:
        return {"on": vs.active, "busy": True, "status": busy, "server": active.name if active else "", "chips": []}
    if vs.running:
        how = "весь трафик" if vs.mode == "tun" else "системный прокси"
        chips = ["AmneziaWG" if vs.kind == "awg" else "VLESS", how]
        if vs.singbox and vs.singbox.running and core.settings.get("vpn", "ru_direct"):
            chips.append("российское — напрямую")
        if vs.killswitch:
            chips.append("kill switch")
        if vs.kind == "vless" and vs.current:
            server = ("Авто: " if vs.selected == vpn.AUTO_TAG else "") + _server_name(app, vs.current)
            ping = app.pings.get(vs.current)
        else:
            server, ping = (active.name if active else "—"), None
        return {"on": True, "status": "Подключён", "server": server, "ping": ping, "chips": chips,
                "since": app.vpn_since, "history": list(app.history)}
    if vs.reconnecting:
        return {"on": True, "busy": True, "status": "Переподключение…", "server": active.name if active else "",
                "hint": "Пока VPN не подключится, " + ("интернет мимо VPN закрыт (kill switch)." if vs.killswitch
                                                         else "программы ходят в интернет напрямую.")}
    if vs.killswitch:
        return {"on": False, "status": "Интернет закрыт", "server": active.name if active else "",
                "hint": "Строгий kill switch: пока VPN выключен, интернет закрыт для всех программ, кроме BypassHub."}
    if auto:
        return {"on": False, "status": "Выключен", "server": "Авто — лучший сервер"}
    if active is None:
        return {"on": False, "status": "Нет серверов", "server": "", "hint": "Добавьте свой ключ во вкладке VPN"}
    return {"on": False, "status": "Выключен", "server": f"Сервер: {active.name}"}


def _server_name(app, pid: str) -> str:
    return next((p.name for p in app.core.vpn_profiles() if p.id == pid), pid)


def zapret_state(app) -> Dict[str, Any]:
    zs, core = app.zs, app.core
    busy = app.busy.get("zapret")
    if busy:
        return {"on": zs.running, "busy": True, "status": busy, "sub": ""}
    if not zs.installed:
        return {"on": False, "status": "Не установлен", "sub": "Скачивается…"}
    if zs.running:
        strategy = (zs.service_strategy + ".bat") if zs.service_strategy else core.strategy
        return {"on": True, "status": "Работает", "sub": "Стратегия " + strategy.removesuffix(".bat")}
    if zs.service_installed:
        return {"on": True, "warn": True, "status": "Служба не работает", "sub": "Выберите другую стратегию"}
    return {"on": False, "status": "Выключен", "sub": "Обход блокировок Discord и YouTube"}


def tg_state(app) -> Dict[str, Any]:
    ts = app.ts
    busy = app.busy.get("tg")
    if busy:
        return {"on": ts.running, "busy": True, "status": busy, "sub": ""}
    if not ts.installed:
        return {"on": False, "status": "Не установлен", "sub": "Скачивается…"}
    if ts.running:
        cfg = app.core.tg.load_config()
        if not ts.listening:
            return {"on": True, "warn": True, "status": "Порт не отвечает", "sub": "Проверьте лог прокси"}
        return {"on": True, "status": "Работает", "sub": f"Прокси {cfg.get('host')}:{cfg.get('port')}"}
    return {"on": False, "status": "Выключен", "sub": "Telegram без VPN"}


def servers(app) -> List[Dict[str, Any]]:
    core, vs = app.core, app.vs
    profiles = core.vpn_profiles()
    selected = vpn.AUTO_TAG if core.vpn_is_auto() else (core.vpn_active().id if core.vpn_active() else "")
    live = vs.running and vs.kind == "vless"
    out: List[Dict[str, Any]] = []
    if len(core.vpn_vless()) >= 2:
        cur = vs.current if live and vs.selected == vpn.AUTO_TAG else ""
        out.append({"id": vpn.AUTO_TAG, "name": "Авто — лучший сервер",
                    "kind": f"сейчас: {_server_name(app, cur)}" if cur else "Выбирает самый быстрый сам",
                    "active": selected == vpn.AUTO_TAG, "ping": app.pings.get(cur) if cur else None})
    for p in profiles:
        kind = "Hysteria2" if p.data.lower().startswith(vpn.HYSTERIA2_SCHEMES) else KIND_LABELS.get(p.kind, p.kind)
        ping = None
        if p.kind != "awg" and p.id in app.pings:
            ping = app.pings[p.id] if app.pings[p.id] is not None else False
        out.append({"id": p.id, "name": p.name, "kind": kind, "code": _country(p.name), "active": p.id == selected,
                    "ping": ping, "now": live and vs.current == p.id and vs.selected == vpn.AUTO_TAG})
    return out


def ip_state(app) -> Dict[str, Any]:
    ip = app.ip
    if ip is None:
        return {"checking": "check" in app.busy}
    return {"ip": ip.ip, "country": ip.country_name, "code": ip.country if ip.country != ip.country_name else "",
            "viaVpn": app.vs.running, "checked": app.ip_at}


def avail(app) -> List[Dict[str, Any]]:
    out = []
    for name, icon in AVAIL:
        st = app.avail.get(name)
        if "check" in app.busy:
            state, ms = "wait", None
        elif st is None:
            state, ms = "none", None
        else:
            state = "ok" if st.ok else ("part" if st.partial else "bad")
            ms = st.ms if st.ok else None
        out.append({"name": name, "icon": icon, "state": state, "ms": ms})
    return out


def updates_state(app) -> Dict[str, Any]:
    pending = [f"{i.title} {i.latest}" for i in app.infos if i.available and i.installed]
    note = "Проверка…" if "updates" in app.busy else ("Всё актуально" if app.infos else "")
    return {"version": __version__, "pending": pending, "note": note}


def state(app) -> Dict[str, Any]:
    s = app.core.settings
    return {"vpn": vpn_state(app), "zapret": zapret_state(app), "tg": tg_state(app),
            "quick": {"ru": bool(s.get("vpn", "ru_direct")), "ks": bool(s.get("vpn", "killswitch"))},
            "servers": servers(app), "ip": ip_state(app), "avail": avail(app), "updates": updates_state(app),
            "checking": "check" in app.busy, "pinging": "ping" in app.busy}


# ------------------------------------------------------------------ действия

def _quick(key: str):
    def fn(app, value):
        app.core.settings.set("vpn", key, bool(value))
        if key == "ru_direct" or app.vs.mode == "tun" or app.vs.reconnecting:
            app.apply_vpn()
    return fn


def _tg_open(app, _):
    from ... import tgproxy
    url = tgproxy.proxy_link(app.core.tg.load_config())
    if not winutil.open_url(url):
        app.clipboard_set(url)
        return ui.info("Не удалось открыть Telegram. Ссылка скопирована — отправьте её себе в «Избранное» и "
                       "нажмите на неё.\n\n" + url)
    return {}


ACTIONS = {
    "vpn": lambda app, v: app.toggle("vpn"),
    "zapret": lambda app, v: app.toggle("zapret"),
    "tg": lambda app, v: app.toggle("tg"),
    "vpn.power": lambda app, v: app.toggle("vpn", bool(v)),
    "zapret.power": lambda app, v: app.toggle("zapret", bool(v)),
    "tg.power": lambda app, v: app.toggle("tg", bool(v)),
    "check": lambda app, v: app.check_services(),
    "ping": lambda app, v: app.ping_servers(),
    "server": lambda app, v: app.select_server(str(v)),
    "ru_direct": _quick("ru_direct"),
    "killswitch": _quick("killswitch"),
    "update_all": lambda app, v: app.run_updates(force_install=True),
    "tg.open": _tg_open,
}
