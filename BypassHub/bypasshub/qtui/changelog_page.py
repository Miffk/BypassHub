"""Что нового: изменения по версиям (из bypasshub/changelog.py)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .. import __version__
from ..changelog import CHANGES
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from .app import App

OPEN = 3  # столько последних версий показаны раскрытыми; более старые — свёрнуты


class ChangelogPage(Page):
    def __init__(self, app: "App"):
        super().__init__(app)
        W.page_title(self.lay, "Что нового", f"Установлена версия {__version__}")
        for i, rel in enumerate(CHANGES):
            title = f"Версия {rel.version}" + (" — установлена" if rel.version == __version__ else "")
            body = W.section(self.lay, title, rel.date, collapsible=i >= OPEN, key=f"changelog.{rel.version}")
            for item in rel.items:
                body.addWidget(W.label("•  " + item, wrap=True))
        self.finish()
