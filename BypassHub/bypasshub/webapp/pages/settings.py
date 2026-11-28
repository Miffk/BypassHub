"""Настройки (как qtui/settings_page.py): оформление, запуск и окно, горячие клавиши, резервная копия."""
from __future__ import annotations

import time
from pathlib import Path

from ... import APP_NAME, __version__, backup, hotkeys, winutil
from ... import palette as T
from .. import ui

MODES = {"dark": "Тёмная", "light": "Светлая", "system": "Как в Windows" if winutil.IS_WINDOWS else "Как в системе"}
APP_SWITCHES = (("start_minimized", "Запускать свёрнутым в трей"),
                ("restore_state", "Включать то, что было включено (zapret, прокси)"),
                ("close_to_tray", "Кнопка «закрыть» сворачивает в трей"),
                ("stop_on_exit", "При выходе выключать zapret (режим «Программа») и прокси"))


def _a(app) -> dict:
    return app.core.settings.data["appearance"]


def build(app):
    a = _a(app)
    s = app.core.settings
    st = app.page_state["settings"]
    if "autostart" not in st and "autostart" not in app.busy:
        app.run("autostart", winutil.autostart_enabled, lambda on: st.__setitem__("autostart", bool(on)), "", "")
    key = T.preset_key_for(a)
    presets = [{"key": name, "name": name, "colors": cols, "selected": key == name} for name, cols in T.PRESETS.items()]
    saved = a.get("saved") or []
    mine = [{"id": str(i), "title": t.get("name", ""), "code": "", "icon": "",
             "click": "theme.saved", "arg": i, "active": key == T.MY_PREFIX + t.get("name", ""),
             "swatch": T.normalize_colors(t.get("colors")),
             "actions": [{"id": "theme.rename", "icon": "edit", "arg": i, "tip": "Переименовать"},
                         {"id": "theme.delete", "icon": "trash", "arg": i, "tip": "Удалить"}]}
            for i, t in enumerate(saved)]
    blocks = [ui.section("Оформление", [
        ui.segmented("theme.mode", list(MODES.values()), MODES.get(a.get("mode", "dark"), MODES["dark"]), "Тема"),
        {"type": "swatches", "id": "theme.preset", "options": presets},
        ui.text("Мои темы", "muted"),
        ui.listing("mythemes", mine, "Здесь появятся ваши темы: выберите свои цвета — и нажмите «Сохранить текущие "
                                     "цвета»."),
        ui.buttons(ui.button("theme.save", "Сохранить текущие цвета", icon="plus")),
        ui.text("Свои цвета (до трёх — градиент)", "muted"),
        {"type": "colors", "id": "theme.color", "colors": T.normalize_colors(a.get("colors"))},
        {"type": "slider", "id": "theme.tint", "label": "Свечение фона", "value": int(a.get("tint", 70)), "min": 0,
         "max": 100, "unit": "%"},
    ], help="Выберите тему и цвета — в них окрасится всё окно: свечение фона, кнопки, переключатели, меню.")]

    blocks.append(ui.section("Запуск и окно", [
        ui.toggle("autostart", "Запускать при включении компьютера", st.get("autostart", s.get("app", "autostart")),
                  help="Ярлык в ~/.config/autostart. Если при запуске нужно включить то, что требует прав "
                       "администратора, система один раз спросит пароль." if winutil.IS_LINUX else
                       "Через Планировщик заданий Windows — с правами администратора и без запроса UAC.",
                  disabled="autostart" in app.busy),
        *[ui.toggle(f"app.{k}", text, s.get("app", k)) for k, text in APP_SWITCHES],
        ui.toggle("ui.web", "Новый интерфейс (это окно)", s.get("app", "ui") != "qt",
                  help="Выключите, чтобы вернуться к прежнему окну. Изменение — после перезапуска BypassHub."),
    ]))

    h = s.data["hotkeys"]
    if winutil.IS_LINUX:
        from ... import linux
        base = linux.shell_join(linux.self_command())
        hk_items = [ui.text("Назначаются в системе на эти команды:", "muted")]
        for action, text in hotkeys.ACTIONS.items():
            hk_items += [ui.text(text), ui.text(f"{base} --action {action}", "small")]
        blocks.append(ui.section("Горячие клавиши", hk_items, collapsible=True, key="settings.hotkeys",
                                 help="KDE Plasma: Параметры системы → Клавиатура → Комбинации клавиш → «Добавить» → "
                                      "«Команда или сценарий». GNOME: Настройки → Клавиатура → Комбинации клавиш → "
                                      "«Свои комбинации»."))
    else:
        failed = st.get("hk_failed") or []
        bindings = h.get("bindings") or {}
        blocks.append(ui.section("Горячие клавиши", [
            ui.toggle("hotkeys.enabled", "Включить горячие клавиши", h.get("enabled")),
            {"type": "hotkeys", "rows": [{"id": act, "label": text, "value": bindings.get(act, "")}
                                         for act, text in hotkeys.ACTIONS.items()]},
            ui.text("Заняты другой программой: " + ", ".join(failed) + ". Выберите другие сочетания.", color="red")
            if failed else None,
            ui.buttons(ui.button("hotkeys.defaults", "По умолчанию")),
        ], help="Работают в любой программе, даже когда BypassHub свёрнут в трей. Нажмите на сочетание, чтобы "
                "изменить его, затем нажмите новые клавиши (Esc — отмена). Нужен хотя бы один из модификаторов "
                "Ctrl или Alt.", collapsible=True, key="settings.hotkeys",
            note=("включены" if h.get("enabled") else "выключены") + (" · есть конфликты" if failed else "")))

    blocks.append(ui.section("Данные и резервная копия", [
        ui.text(f"Папка: {app.core.paths.root}", "muted"),
        ui.buttons(ui.button("data.open", "Открыть папку", icon="folder"), ui.button("data.export", "Экспорт настроек"),
                   ui.button("data.import", "Импорт")),
    ], help="Резервная копия — все настройки в одном файле: BypassHub, zapret (Game Filter, IPSet, фейки, списки, "
            "исключения) и TG WS Proxy. Удобно для переноса на другой компьютер. В файле есть secret прокси — не "
            "публикуйте его.", collapsible=True, key="settings.data"))
    blocks.append(ui.section("О программе", [
        ui.text(f"{APP_NAME} {__version__} — zapret-discord-youtube, tg-ws-proxy и свой VPN (VLESS, Hysteria2, "
                "AmneziaWG) в одном окне."),
        ui.text("Сами проекты принадлежат их авторам (Flowseal, bol-van, SagerNet, Amnezia). BypassHub скачивает "
                "только официальные релизы с GitHub.", "muted"),
        ui.buttons(ui.button("url", "zapret-discord-youtube", arg="https://github.com/Flowseal/zapret-discord-youtube"),
                   ui.button("url", "tg-ws-proxy", arg="https://github.com/Flowseal/tg-ws-proxy")),
    ], collapsible=True, key="settings.about"))
    return ui.page("Настройки", blocks)


# ------------------------------------------------------------------ оформление

def _save(app):
    app.core.settings.save()


def _mode(app, text):
    _a(app)["mode"] = next((k for k, v in MODES.items() if v == text), "dark")
    _save(app)


def _preset(app, name):
    if name in T.PRESETS:
        a = _a(app)
        a["preset"], a["colors"] = name, list(T.PRESETS[name])
        _save(app)


def _saved(app, index):
    saved = _a(app).get("saved") or []
    i = int(index)
    if i < len(saved):
        a = _a(app)
        a["colors"] = T.normalize_colors(saved[i].get("colors"))
        a["preset"] = T.MY_PREFIX + saved[i].get("name", "")
        _save(app)


def _theme_save(app, _):
    a = _a(app)
    a["preset"] = T.remember_theme(a)
    _save(app)
    return ui.done("Тема сохранена в «Мои темы»")


def _theme_delete(app, index):
    a = _a(app)
    saved = a.get("saved") or []
    i = int(index)
    if i < len(saved):
        saved.pop(i)
        a["preset"] = T.preset_key_for(a)
        _save(app)


def _theme_rename(app, index):
    saved = _a(app).get("saved") or []
    i = int(index)
    if i >= len(saved):
        return {}
    return {"prompt": "Название темы:", "title": "Переименовать тему", "value": saved[i].get("name", ""),
            "then": "theme.rename.do", "arg": i}


def _theme_rename_do(app, value):
    a = _a(app)
    saved = a.get("saved") or []
    i, new = int((value or {}).get("arg", -1)), str((value or {}).get("text", "")).strip()[:24]
    if not (0 <= i < len(saved)) or not new:
        return {}
    old = saved[i].get("name", "")
    if new != old and any(t.get("name") == new for t in saved):
        return ui.error(f"Тема «{new}» уже есть.")
    saved[i]["name"] = new
    if a.get("preset") == T.MY_PREFIX + old:
        a["preset"] = T.MY_PREFIX + new
    _save(app)
    return {}


def _color(app, value):
    a = _a(app)
    colors = T.normalize_colors(a.get("colors"))
    i, c = int((value or {}).get("index", 0)), str((value or {}).get("color", ""))
    if 0 <= i < len(colors) and T.normalize_colors([c]) == [c.lower()]:
        colors[i] = c.lower()
        a["colors"] = colors
        a["preset"] = T.preset_key_for(a)
        _save(app)


def _color_add(app, _):
    a = _a(app)
    colors = T.normalize_colors(a.get("colors"))
    if len(colors) < 3:
        colors.append(T.mix(colors[-1], "#ffffff", 0.35) if colors else "#2b7fff")
        a["colors"] = colors
        a["preset"] = T.preset_key_for(a)
        _save(app)


def _color_remove(app, index):
    a = _a(app)
    colors = T.normalize_colors(a.get("colors"))
    i = int(index)
    if len(colors) > 1 and 0 <= i < len(colors):
        colors.pop(i)
        a["colors"] = colors
        a["preset"] = T.preset_key_for(a)
        _save(app)


def _tint(app, value):
    _a(app)["tint"] = max(0, min(100, int(value)))
    _save(app)


# ------------------------------------------------------------------ запуск и окно

def _autostart(app, on):
    st = app.page_state["settings"]

    def work():
        winutil.set_autostart(bool(on))
        app.core.settings.set("app", "autostart", bool(on))
        st["autostart"] = bool(on)
    app.run("autostart", work, None, "", "Автозапуск")


def _app_switch(key):
    return lambda app, on: app.core.settings.set("app", key, bool(on))


def _ui_web(app, on):
    app.core.settings.set("app", "ui", "web" if on else "qt")
    return ui.info("Прежнее окно откроется после перезапуска BypassHub." if not on else "Готово.")


# ------------------------------------------------------------------ горячие клавиши

def _hk_apply(app):
    app.core.settings.save()
    app.page_state["settings"]["hk_failed"] = app.apply_hotkeys()


def _hk_enabled(app, on):
    app.core.settings.data["hotkeys"]["enabled"] = bool(on)
    _hk_apply(app)


def _hk_set(app, value):
    v = value or {}
    action, combo = str(v.get("id", "")), hotkeys.normalize(str(v.get("combo", "")))
    if action not in hotkeys.ACTIONS:
        return {}
    if not combo:
        return ui.error("Нужно сочетание с Ctrl или Alt, например Ctrl+Alt+Z")
    bindings = app.core.settings.data["hotkeys"].setdefault("bindings", {})
    other = next((a for a, c in bindings.items() if c == combo and a != action), None)
    if other:
        return ui.error(f"{combo} уже назначено на «{hotkeys.ACTIONS[other]}».")
    bindings[action] = combo
    _hk_apply(app)
    return {}


def _hk_clear(app, action):
    app.core.settings.data["hotkeys"].setdefault("bindings", {})[str(action)] = ""
    _hk_apply(app)


def _hk_defaults(app, _):
    app.core.settings.data["hotkeys"]["bindings"] = dict(hotkeys.DEFAULT_BINDINGS)
    _hk_apply(app)


# ------------------------------------------------------------------ резервная копия

def _export(app, _):
    path = app.save_file("Сохранить настройки", f"BypassHub-settings-{time.strftime('%Y%m%d')}.json")
    if not path:
        return {}
    core = app.core
    backup.export_file(Path(path), core.settings.data, core.zapret, core.tg)
    return ui.info(f"Настройки сохранены:\n{path}")


def _import(app, _):
    paths = app.pick_files("Загрузить настройки", "Настройки BypassHub (*.json)")
    if not paths:
        return {}
    core = app.core

    def work():
        warnings = backup.import_file(Path(paths[0]), core.settings, core.zapret, core.tg)
        try:
            winutil.set_autostart(bool(core.settings.get("app", "autostart")))
        except Exception as exc:
            warnings.append(f"автозапуск: {exc}")
        core.zapret_apply()
        core.tg_apply()
        core.update_learner()
        return warnings

    def done(warnings):
        app.apply_hotkeys()
        app.alert("Настройки загружены." + ("\n\n" + "\n".join(warnings) if warnings else ""), "Импорт")
    app.run("import", work, done, "", "Импорт настроек")
    return {}


ACTIONS = {
    "theme.mode": _mode, "theme.preset": _preset, "theme.saved": _saved, "theme.save": _theme_save,
    "theme.delete": _theme_delete, "theme.rename": _theme_rename, "theme.rename.do": _theme_rename_do,
    "theme.color": _color, "theme.color.add": _color_add, "theme.color.remove": _color_remove, "theme.tint": _tint,
    "autostart": _autostart, "ui.web": _ui_web, "hotkeys.enabled": _hk_enabled, "hotkey.set": _hk_set,
    "hotkey.clear": _hk_clear, "hotkeys.defaults": _hk_defaults,
    "data.open": lambda app, v: winutil.open_path(app.core.paths.root), "data.export": _export,
    "data.import": _import, "url": lambda app, url: app.open_url(str(url)),
    **{f"app.{k}": _app_switch(k) for k, _ in APP_SWITCHES},
}
