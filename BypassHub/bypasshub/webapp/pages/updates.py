"""Страница «Обновления» (как qtui/updates_page.py): компоненты, проверка и установка, параметры."""
from __future__ import annotations

import time

from ... import __version__, awg, selfupdate, tgproxy, vpn, winutil, zapret
from .. import ui

INTERVALS = {0: "Только при запуске", 1: "Каждый час", 3: "Каждые 3 часа", 6: "Каждые 6 часов",
             12: "Каждые 12 часов", 24: "Раз в сутки"}
REPOS = {"zapret": zapret.REPO, "tg": tgproxy.REPO, "singbox": vpn.REPO, "awg": awg.REPO, "self": selfupdate.REPO}
TITLES = {"zapret": "Zapret", "tg": "TG WS Proxy", "singbox": "VPN: sing-box (VLESS)", "awg": "VPN: AmneziaWG",
          "self": "BypassHub"}
ICONS = {"zapret": "shield", "tg": "send", "singbox": "vpn", "awg": "lock", "self": "bolt"}
LAZY = {"singbox", "awg"}  # движки VPN скачиваются только при первом включении VPN


def _assets():
    out = {"auto": "Автоматически"}
    out.update(tgproxy.ASSETS)
    return out


def build(app):
    core = app.core
    s = core.settings
    infos = {i.component: i for i in app.infos}
    st = app.page_state["updates"]
    infos.update(st.get("infos", {}))
    installed = {"zapret": core.zapret.local_version() if core.zapret.is_installed() else "",
                 "tg": core.tg.local_version() if core.tg.is_installed() else "",
                 "singbox": core.singbox.local_version() if core.singbox.is_installed() else "",
                 "awg": core.awg.local_version() if core.awg.is_installed() else "", "self": __version__}
    busy = "updates" in app.busy
    rows = []
    for key, title in TITLES.items():
        info = infos.get(key)
        latest = info.latest if info and info.latest else "?"
        ver = (f"установлена: {installed[key] or 'не установлен'} (выбрана вручную)" if info and info.pinned
               else f"установлена: {installed[key] or 'не установлен'} · последняя: {latest}")
        if key in app.progress:
            state, color = "Загрузка: " + app.progress[key], "accent"
        elif info is None and key in LAZY and not installed[key]:
            state, color = "Скачается при первом включении VPN", "muted"
        elif info is None:
            state, color = "", "muted"
        elif info.error:
            state, color = "Не удалось проверить: " + info.error, "red"
        elif info.pinned:
            state, color = "Выбрана вручную — сменить на странице Zapret", "muted"
        elif info.available:
            state, color = "Доступно обновление", "yellow"
        else:
            state, color = "Актуальная версия", "green"
        avail = bool(info and info.available)
        if key == "awg" and winutil.IS_LINUX:
            label, disabled, state, color = "Встроен", True, "Встроен в BypassHub, обновляется вместе с ним", "muted"
        elif key == "self":
            label, disabled = "Обновить", not (avail and not busy and selfupdate.is_frozen())
        else:
            label = "Обновить" if avail and installed[key] else ("Установить" if not installed[key] else "Переустановить")
            disabled = busy
        rows.append({"id": key, "title": title, "subtitle": ver, "code": "", "icon": ICONS[key], "right": state,
                     "rightColor": color, "actions": [
                         {"id": "install", "label": label, "primary": avail, "disabled": disabled},
                         {"id": "github", "icon": "link", "tip": "Релизы на GitHub"}]})
    last = int(s.get("updates", "last_check") or 0)
    status = app.busy.get("updates") or st.get("status") or ("Последняя проверка: " + (
        time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "ещё не было"))
    assets = _assets()
    blocks = [
        ui.section("Компоненты", [
            ui.listing("components", rows),
            ui.text(status, "muted", color=st.get("status_color", "")),
            ui.buttons(ui.button("check", "Проверить сейчас", icon="refresh", disabled=busy),
                       ui.button("install_all", "Установить все обновления", primary=True, disabled=busy)),
        ], help="BypassHub сам проверяет GitHub-репозитории проектов и скачивает новые релизы. При обновлении "
                "zapret сохраняются ваши списки, Game Filter, IPSet, фейки и свои стратегии; работающий обход "
                "перезапускается сам."),
        ui.section("Параметры", [
            ui.toggle("check_on_start", "Проверять при запуске", s.get("updates", "check_on_start")),
            ui.toggle("auto_install", "Устанавливать автоматически", s.get("updates", "auto_install")),
            ui.toggle("self_update", "Обновлять сам BypassHub", s.get("updates", "self_update")),
            ui.select("interval", list(INTERVALS.values()),
                      INTERVALS.get(int(s.get("updates", "interval_hours")), INTERVALS[6]), "Периодическая проверка"),
            ui.select("asset", list(assets.values()), assets.get(s.get("tg", "asset"), "Автоматически"),
                      "Сборка TG WS Proxy",
                      help="" if winutil.IS_LINUX else "Если антивирус блокирует основную сборку, попробуйте версию "
                                                       "для Windows 7 — по возможностям она не отличается."),
        ]),
    ]
    return ui.page("Обновления", blocks)


def _install(app, key):
    core = app.core
    st = app.page_state["updates"]

    def progress(component, done, total):
        app.progress[component] = f"{done / 1048576:.1f}" + (f" из {total / 1048576:.1f}" if total else "") + " МБ"

    def work():
        info = next((i for i in app.infos if i.component == key), None)
        if info is None or info.release is None:
            info = {"zapret": core.check_zapret, "tg": core.check_tg, "singbox": core.check_singbox,
                    "awg": core.check_awg, "self": core.check_self}[key]()
        core.install(info, progress)
        return info

    def done(info):
        app.progress.clear()
        st.setdefault("infos", {})[key] = info
        if core.restarting_for_update:
            app.quit()
            return ""
        st["status"], st["status_color"] = f"{info.title} {info.latest} установлен", "green"
        return f"{info.title} {info.latest} установлен"
    app.run("updates", work, done, "Получение информации о релизе…", "Обновление")


def _setting(key):
    return lambda app, on: app.core.settings.set("updates", key, bool(on))


ACTIONS = {
    "check": lambda app, v: app.run_updates(),
    "install_all": lambda app, v: app.run_updates(force_install=True),
    "install": _install,
    "github": lambda app, key: app.open_url(f"https://github.com/{REPOS[key]}/releases"),
    "check_on_start": _setting("check_on_start"), "auto_install": _setting("auto_install"),
    "self_update": _setting("self_update"),
    "interval": lambda app, t: app.core.settings.set("updates", "interval_hours",
                                                     next((h for h, v in INTERVALS.items() if v == t), 6)),
    "asset": lambda app, t: app.core.settings.set("tg", "asset", next((k for k, v in _assets().items() if v == t),
                                                                      "auto")),
}
