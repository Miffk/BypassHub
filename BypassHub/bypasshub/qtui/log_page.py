"""Журнал: события программы, zapret и прокси."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .. import winutil
from ..log import memory_handler
from . import widgets as W

if TYPE_CHECKING:
    from .app import App


class LogPage(QWidget):
    MAX_LINES = 5000

    def __init__(self, app: "App"):
        super().__init__()
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, W.PAGE_MARGIN, 18)
        W.page_title(lay, "Журнал", "События программы, zapret и прокси")
        self.box = W.textbox(200, readonly=True)
        self.box.setMaximumBlockCount(self.MAX_LINES)
        lay.addWidget(self.box, 1)
        lay.addLayout(W.button_bar(
            W.button("Открыть файл лога", lambda: winutil.open_path(app.core.paths.log_file)),
            W.button("Лог nfqws" if winutil.IS_LINUX else "Лог winws.exe", lambda: self._open(app.core.paths.winws_log)),
            W.button("Лог прокси", lambda: self._open(app.core.tg.log_file)),
            W.button("Очистить", self.box.clear)))
        self.box.setPlainText("\n".join(memory_handler.lines))
        self._unsubscribe = memory_handler.subscribe(lambda line: app.call_ui(lambda: self._append(line)))
        self.destroyed.connect(lambda: self._unsubscribe())

    def _append(self, line: str) -> None:
        bar = self.box.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.box.appendPlainText(line)
        if at_end:
            self.box.moveCursor(QTextCursor.MoveOperation.End)

    def _open(self, path) -> None:
        if path.exists():
            winutil.open_path(path)
        else:
            self.app.info("Файл ещё не создан.")
