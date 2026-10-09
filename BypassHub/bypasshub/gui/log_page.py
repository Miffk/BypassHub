from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from .. import winutil
from ..log import memory_handler
from . import widgets as W

if TYPE_CHECKING:
    from .app import App


class LogPage(ctk.CTkFrame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, fg_color=W.P.window_bg)
        self.app = app
        W.page_title(self, "Журнал", "События программы, zapret и прокси")
        self.box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12), wrap="word")
        self.box.pack(fill="both", expand=True, padx=4)
        bar = W.button_bar(self)
        W.add_button(bar, "Открыть файл лога", lambda: winutil.open_path(app.core.paths.log_file),
                     secondary=True, width=170)
        W.add_button(bar, "Лог winws.exe", lambda: self._open(app.core.paths.winws_log), secondary=True)
        W.add_button(bar, "Лог прокси", lambda: self._open(app.core.tg.log_file), secondary=True)
        W.add_button(bar, "Очистить", lambda: W.set_text(self.box, "", readonly=True), secondary=True)

        W.set_text(self.box, "\n".join(memory_handler.lines) + ("\n" if memory_handler.lines else ""),
                   readonly=True)
        memory_handler.subscribe(lambda line: app.call_ui(lambda: self._append(line)))

    def _append(self, line: str) -> None:
        self.box.configure(state="normal")
        self.box.insert("end", line + "\n")
        self.box.configure(state="disabled")
        self.box.see("end")

    def _open(self, path) -> None:
        if path.exists():
            winutil.open_path(path)
        else:
            self.app.info("Файл ещё не создан.")
