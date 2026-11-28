"""Страница VPN (как qtui/vpn_page.py): серверы и подписки, добавление ключей, режим, kill switch,
программы мимо VPN, движки."""
from __future__ import annotations

import re
import time
from pathlib import Path

from ... import vpn, winutil
from ...killswitch import KILLSWITCH_DETAILS, KILLSWITCH_INTRO, STRICT_DETAILS, STRICT_INTRO
from .. import ui
from .home import KIND_LABELS, _country, vpn_state

PROXY_HINT = ("«Весь трафик» — через VPN идёт всё (виртуальный сетевой адаптер). «Системный прокси» — только "
              "браузеры и программы, которые учитывают настройки прокси Windows, остальные работают как обычно.")


def _when(ts: float) -> str:
    now, t = time.localtime(), time.localtime(ts)
    return ("в " + time.strftime("%H:%M", t)) if t[:3] == now[:3] else time.strftime("%d.%m в %H:%M", t)


def _rules_age(manager) -> str:
    files = [p for p in manager.rule_set_paths().values() if p.exists()]
    if not files:
        return ""
    return "от " + time.strftime("%d.%m.%Y", time.localtime(max(p.stat().st_mtime for p in files)))


def _exclude_state(core):
    names = vpn.clean_exe_names(core.settings.get("vpn", "exclude_apps") or [])
    presets = {e for exes in vpn.EXCLUDE_PRESETS.values() for e in exes}
    custom = vpn.clean_exe_names((core.settings.get("vpn", "exclude_custom") or []) + [n for n in names
                                                                                        if n not in presets])
    return names, [c for c in custom if c not in presets]


def build(app):
    core, vs = app.core, app.vs
    s = core.settings
    head = vpn_state(app)
    color = "yellow" if head.get("busy") else ("green" if head["on"] else "muted")
    status = head["status"] + (f": {head['server']}" if head["on"] and not head.get("busy") and head.get("server")
                               else "")
    details = " · ".join(head.get("chips") or []) or head.get("hint", "")
    blocks = [ui.service("vpn", "VPN", "vpn", head["on"], status, color, "Свой сервер: VLESS, Hysteria2 или AmneziaWG",
                         details, busy=bool(head.get("busy")))]

    profiles = core.vpn_profiles()
    selected = vpn.AUTO_TAG if core.vpn_is_auto() else (core.vpn_active().id if core.vpn_active() else "")
    rows = []
    if len(core.vpn_vless()) >= 2:
        rows.append({"id": vpn.AUTO_TAG, "title": "Авто — лучший сервер", "code": "", "icon": "bolt",
                     "subtitle": "Сам выбирает самый быстрый сервер (VLESS или Hysteria2) и переключается, если он "
                                 "перестал отвечать", "active": selected == vpn.AUTO_TAG, "click": "server",
                     "arg": vpn.AUTO_TAG})
    for p in profiles:
        kind = "Hysteria2" if p.data.lower().startswith(vpn.HYSTERIA2_SCHEMES) else KIND_LABELS.get(p.kind, p.kind)
        right, rc = "", None
        if p.kind != "awg" and p.id in app.pings:
            ms = app.pings[p.id]
            right = "нет ответа" if ms is None else f"{ms:.0f} мс"
            rc = "red" if ms is None else ("green" if ms < 150 else ("yellow" if ms < 350 else "red"))
        rows.append({"id": p.id, "title": p.name, "badge": kind + (" · из подписки" if p.sub else ""),
                     "subtitle": p.summary, "code": _country(p.name), "active": p.id == selected, "click": "server",
                     "arg": p.id, "right": right, "rightColor": rc,
                     "actions": [{"id": "rename", "icon": "edit", "tip": "Переименовать"},
                                 {"id": "remove", "icon": "trash", "tip": "Удалить",
                                  "confirm": f"Удалить сервер «{p.name}»?"}]})
    subs = s.get("vpn", "subscriptions") or []
    sub_rows = [{"id": url, "title": url if len(url) <= 60 else url[:57] + "…", "code": "", "icon": "link",
                 "subtitle": ("обновлена " + _when(core.vpn_sub_checked(url))) if core.vpn_sub_checked(url) else "",
                 "actions": [{"id": "sub.remove", "icon": "trash", "tip": "Удалить подписку",
                              "confirm": "Удалить подписку и все её серверы?\n" + url}]} for url in subs]
    blocks.append(ui.section("Серверы", [
        ui.listing("servers", rows, "Серверов пока нет — добавьте ключ ниже."),
        ui.text("Подписки", "muted") if subs else None,
        ui.listing("subs", sub_rows) if subs else None,
        ui.buttons(ui.button("ping", "Проверка…" if "ping" in app.busy else "Проверить пинг",
                             disabled="ping" in app.busy or not core.vpn_vless()),
                   ui.button("subs.refresh", "Обновляю…" if "subs" in app.busy else "Обновить подписки",
                             icon="refresh", disabled=not subs or "subs" in app.busy)),
    ], help="Нажмите на сервер, чтобы выбрать его. Если VPN включён, он переключится сразу. «Авто» сам выбирает "
            "самый быстрый сервер VLESS или Hysteria2."))

    blocks.append(ui.section("Добавить сервер", [
        ui.textarea("key", "", "vless://…  hysteria2://…  vpn://…  или ссылка на подписку https://…", rows=2),
        ui.buttons(ui.button("add", "Добавить", primary=True, collect=True, disabled="add" in app.busy),
                   ui.button("add.clipboard", "Вставить из буфера"), ui.button("add.files", "Файлы .conf…",
                                                                               icon="folder")),
    ], help="Ключ vless://… или hysteria2://…, ключ AmneziaVPN vpn://… (AmneziaVPN → «Поделиться»), конфиг "
            "AmneziaWG (.conf) или ссылка на подписку https://… от VPN-сервиса. Можно несколько ключей сразу — по "
            "одному на строку.<br><br>Ключ — это пароль от вашего сервера: не публикуйте его и не отправляйте "
            "посторонним."))

    mode = s.get("vpn", "mode")
    blocks.append(ui.section("Режим", [
        ui.segmented("mode", list(vpn.MODES.values()), vpn.MODES.get(mode, vpn.MODES["tun"]), "Что пускать через VPN",
                     help=PROXY_HINT),
        ui.field("port", str(s.get("vpn", "proxy_port")), "Порт системного прокси", width=110,
                 buttons=[ui.button("port.apply", "Применить", small=True)]) if mode == "proxy" else None,
        ui.toggle("ru_direct", "Российское — напрямую", s.get("vpn", "ru_direct"),
                  help="Раздельное туннелирование. Через VPN идёт всё зарубежное. Напрямую, с домашнего IP, — сайты "
                       ".ru, .рф, .su со всеми поддоменами, российские IP-адреса и российские сервисы на «нерусских» "
                       "доменах (VK, Яндекс, банки и др.) по базам roscomvpn. YouTube, Telegram, GitHub и Google Play "
                       "всегда идут через VPN."),
        ui.toggle("killswitch", "Kill switch: без VPN — без интернета", s.get("vpn", "killswitch"),
                  help=KILLSWITCH_INTRO + "<br><br>" + KILLSWITCH_DETAILS),
        ui.toggle("strict", "Строгий режим: без интернета, даже когда VPN выключен", s.get("vpn", "killswitch_strict"),
                  help=STRICT_INTRO + "<br><br>" + STRICT_DETAILS,
                  confirm_on="Строгий режим: пока VPN выключен, интернет будет закрыт для всех программ, кроме "
                             "BypassHub и локальной сети. Он откроется, когда вы включите VPN или закроете "
                             "BypassHub.\n\nВключить?"),
    ]))

    names, custom = _exclude_state(core)
    opts = [{"value": "preset:" + title, "label": title, "checked": all(e in names for e in exes)}
            for title, exes in vpn.EXCLUDE_PRESETS.items()]
    opts += [{"value": c, "label": c, "checked": c in names, "removable": True} for c in custom]
    n = sum(1 for o in opts if o["checked"])
    blocks.append(ui.section("Программы мимо VPN", [
        ui.checks("exclude", opts, remove_act="exclude.remove"),
        ui.field("exclude.new", "", placeholder="game, other" if winutil.IS_LINUX else "game.exe, other.exe",
                 buttons=[ui.button("exclude.pick", "Выбрать…"), ui.button("exclude.add", "Добавить", primary=True)]),
    ], help="Отмеченные программы ходят в интернет напрямую, даже когда VPN включён, — например, игры, в которых "
            "через VPN растёт пинг. Работает в режиме «Весь трафик».<br><br>Впишите имя файла программы (можно "
            "несколько через запятую) или нажмите «Выбрать…» и укажите файл игры в папке, куда она установлена.",
        collapsible=True, key="vpn.exclude", note=f"отмечено: {n}" if n else ""))

    sb, aw = core.singbox, core.awg
    blocks.append(ui.section("Движки и журнал", [
        ui.text("sing-box (VLESS, Hysteria2): " + (sb.local_version() if sb.is_installed() else "скачается при включении")),
        ui.text("AmneziaWG: " + (aw.local_version() if aw.is_installed() else "скачается при включении")),
        ui.text("Базы roscomvpn: " + (_rules_age(sb) or "скачаются при включении")),
        ui.buttons(ui.button("log", "Лог VPN"), ui.button("restart", "Перезапустить VPN")),
    ], help="Движки скачиваются с GitHub при первом включении и обновляются вместе с остальными компонентами."
            "<br><br>Если открыт AmneziaVPN, отключитесь в нём перед включением — два VPN одновременно мешают "
            "друг другу.", collapsible=True, key="vpn.engines"))
    return ui.page("VPN", blocks)


# ------------------------------------------------------------------ действия

def _add_many(app, items):
    core = app.core

    def work():
        added, problems = [], []
        for text, name in items:
            got, extra = core.vpn_add(text)
            if name and len(got) == 1 and got[0].kind == "awg":
                core.vpn_rename(got[0].id, name)
                got[0].name = name
            added += got
            problems += [f"{name}: {x}" if name and len(items) > 1 else x for x in extra]
        return added, problems

    def done(result):
        added, problems = result
        msg = []
        if added:
            msg.append("Добавлено серверов: " + str(len(added)) + "\n" + "\n".join("• " + p.name for p in added[:10])
                       + ("\n…" if len(added) > 10 else ""))
        elif not problems:
            msg.append("Эти серверы уже есть в списке.")
        if problems:
            msg.append("Не добавлено:\n" + "\n".join("• " + x for x in problems[:8]))
        app.alert("\n\n".join(msg), "VPN", "" if added else "error")
    app.run("add", work, done, "", "VPN")


def _add(app, form):
    text = str((form or {}).get("key", "") if isinstance(form, dict) else form or "")
    if not text.strip():
        return ui.info("Вставьте ключ в поле выше.")
    _add_many(app, [(text, "")])
    return {"clear": ["key"]}


def _add_clipboard(app, _):
    text = app.clipboard_get()
    if not text.strip():
        return ui.info("Буфер обмена пуст — скопируйте ключ и нажмите ещё раз.")
    _add_many(app, [(text, "")])
    return {}


def _add_files(app, _):
    paths = app.pick_files("Конфиги AmneziaWG", "Конфиг AmneziaWG / WireGuard (*.conf)", many=True)
    if not paths:
        return {}
    files = []
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig") as f:
                files.append((f.read(1024 * 1024), Path(path).stem))  # имя сервера — по имени файла
        except OSError as exc:
            return ui.error(f"{Path(path).name}: {exc}", "Конфиг")
    _add_many(app, files)
    return {}


def _rename(app, pid):
    p = next((x for x in app.core.vpn_profiles() if x.id == pid), None)
    if p is None:
        return {}
    return {"prompt": "Новое название сервера:", "title": "Переименовать", "value": p.name, "then": "rename.do",
            "arg": pid}


def _rename_do(app, value):
    v = value or {}
    if str(v.get("text", "")).strip():
        app.core.vpn_rename(str(v.get("arg")), str(v["text"]))
    return {}


def _remove(app, pid):
    core = app.core
    p = next((x for x in core.vpn_profiles() if x.id == pid), None)
    if p is None:
        return {}
    was_active = core.vpn_active()
    core.vpn_remove(p.id)
    if app.vs.running:
        if not core.vpn_profiles():
            app.toggle("vpn", False)
        elif (was_active is not None and was_active.id == p.id) or p.kind == "vless":
            app.apply_vpn()  # серверы VLESS собраны в одну группу
    return ui.done(f"Сервер «{p.name}» удалён")


def _sub_remove(app, url):
    app.core.vpn_remove_subscription(str(url))
    return ui.done("Подписка удалена")


def _subs_refresh(app, _):
    def done(result):
        total, problems = result
        text = f"Серверов в подписках: {total}"
        if problems:
            text += "\n\n" + "\n".join("• " + x for x in problems[:8])
        app.alert(text, "Подписки")
    app.run("subs", app.core.vpn_refresh_subscriptions, done, "", "Подписки")


def _mode(app, text):
    app.core.settings.set("vpn", "mode", next((k for k, v in vpn.MODES.items() if v == text), "tun"))
    app.apply_vpn()


def _port(app, value):
    try:
        port = int(str(value).strip())
        if not 1024 <= port <= 65535:
            raise ValueError
    except ValueError:
        return ui.error("Порт — число от 1024 до 65535", "VPN")
    app.core.settings.set("vpn", "proxy_port", port)
    if app.vs.mode == "proxy":
        app.apply_vpn()
    return ui.done("Порт сохранён", clear=["port"])


def _strict(app, on):
    core = app.core
    app.run("strict", lambda: core.set_killswitch_strict(bool(on)), None, "", "Kill switch")
    if app.vs.running and app.vs.mode == "tun" and not core.settings.get("vpn", "killswitch"):
        app.apply_vpn()  # при VPN строгий режим работает как kill switch


def _exclude_save(app, names, custom):
    core = app.core
    names, custom = vpn.clean_exe_names(names), vpn.clean_exe_names(custom)
    changed = names != vpn.clean_exe_names(core.settings.get("vpn", "exclude_apps") or [])
    core.settings.set("vpn", "exclude_custom", custom, save=False)
    core.settings.set("vpn", "exclude_apps", names)
    if changed and core.settings.get("vpn", "mode") == "tun":
        app.apply_vpn()


def _exclude_check(app, value):
    v = value or {}
    names, custom = _exclude_state(app.core)
    key, on = str(v.get("value", "")), bool(v.get("on"))
    exes = vpn.EXCLUDE_PRESETS.get(key[7:], []) if key.startswith("preset:") else [key]
    names = [n for n in names if n not in exes] + (list(exes) if on else [])
    _exclude_save(app, names, custom)
    return {}


def _exclude_add(app, text):
    new = vpn.clean_exe_names([x for x in re.split(r"[,;\n]", str(text or "")) if x.strip()])
    if not new:
        return {}
    names, custom = _exclude_state(app.core)
    _exclude_save(app, names + new, custom + new)
    return {"clear": ["exclude.new"]}


def _exclude_remove(app, name):
    names, custom = _exclude_state(app.core)
    _exclude_save(app, [n for n in names if n != name], [c for c in custom if c != name])
    return {}


def _exclude_pick(app, _):
    paths = app.pick_files("Программа мимо VPN", "" if winutil.IS_LINUX else "Программы (*.exe)")
    return _exclude_add(app, paths[0]) if paths else {}


def _log(app, _):
    p = app.core.paths.vpn_log
    if p.exists():
        winutil.open_path(p)
        return {}
    return ui.info("Лог появится после первого включения VPN.")


ACTIONS = {
    "add": _add, "add.clipboard": _add_clipboard, "add.files": _add_files, "rename": _rename,
    "rename.do": _rename_do, "remove": _remove, "sub.remove": _sub_remove, "subs.refresh": _subs_refresh,
    "mode": _mode, "port.apply": _port, "strict": _strict, "exclude": _exclude_check, "exclude.add": _exclude_add,
    "exclude.remove": _exclude_remove, "exclude.pick": _exclude_pick, "log": _log,
    "restart": lambda app, v: app.toggle("vpn", True),
}
