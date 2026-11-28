"""Страницы окна на HTML: описание (build) и действия (act). Каждая страница — свой модуль
с функцией build(app) и словарём ACTIONS {действие: функция(app, значение)}."""
from __future__ import annotations

from typing import Any, Dict

from .. import ui
from . import changelog, home, log_page, settings, tg, updates, vpn, zapret

MODULES = {"zapret": zapret, "tg": tg, "vpn": vpn, "updates": updates, "changelog": changelog,
           "settings": settings, "log": log_page, "home": home}
home_state = home.state

def build(app, page: str) -> Dict[str, Any]:
    mod = MODULES.get(page)
    if mod is None or not hasattr(mod, "build"):
        return ui.page("Нет такой страницы", [])
    return mod.build(app)


def act(app, page: str, action: str, value: Any = None) -> Dict[str, Any]:
    # общие для всех страниц
    if action == "support":
        from ... import SUPPORT_URL
        app.open_url(SUPPORT_URL)
        return {}
    if action == "ui.fold":  # какие сворачиваемые разделы раскрыты — запоминаем
        keys = [k for k, on in (value or {}).items() if on]
        app.core.settings.set("ui", "expanded", keys)
        return {}
    if action == "sheet.close":
        if app.sheet is None or app.sheet.on_close():
            app.sheet = None
        return {}
    if action.startswith("sheet.") and app.sheet is not None and action in app.sheet.actions:
        return app.sheet.actions[action](value) or {}
    for mod in (MODULES.get(page), home):
        fn = getattr(mod, "ACTIONS", {}).get(action) if mod else None
        if fn is not None:
            return fn(app, value) or {}
    return ui.error(f"Неизвестное действие: {action}")
