"""Журнал: события программы, zapret и прокси."""
from __future__ import annotations

from ... import winutil
from ...log import memory_handler
from .. import ui

MAX_LINES = 2000


def build(app):
    lines = list(memory_handler.lines)[-MAX_LINES:]
    st = app.page_state["log"]
    if st.get("cleared") is not None:
        lines = lines[st["cleared"]:] if st["cleared"] <= len(lines) else []
    return ui.page("Журнал", [{"type": "log", "text": "\n".join(lines)}], "События программы, zapret и прокси",
                   actions=[ui.button("open", "Открыть файл лога", icon="folder"),
                            ui.button("winws", "Лог nfqws" if winutil.IS_LINUX else "Лог winws.exe"),
                            ui.button("proxy", "Лог прокси"), ui.button("clear", "Очистить")])


def _open(path):
    def fn(app, _):
        if path(app).exists():
            winutil.open_path(path(app))
            return {}
        return ui.info("Этот лог ещё не создан.")
    return fn


def _clear(app, _):
    app.page_state["log"]["cleared"] = len(list(memory_handler.lines)[-MAX_LINES:])


ACTIONS = {"open": _open(lambda app: app.core.paths.log_file), "winws": _open(lambda app: app.core.paths.winws_log),
           "proxy": _open(lambda app: app.core.tg.log_file), "clear": _clear}
