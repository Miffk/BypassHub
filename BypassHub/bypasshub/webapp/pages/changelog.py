"""Что нового: изменения по версиям (bypasshub/changelog.py)."""
from __future__ import annotations

from ... import __version__
from ...changelog import CHANGES
from .. import ui

OPEN = 3  # столько последних версий раскрыты, остальные свёрнуты


def build(app):
    if app.core.settings.get("app", "seen_changelog") != __version__:
        app.core.settings.set("app", "seen_changelog", __version__)
    blocks = []
    for i, rel in enumerate(CHANGES):
        title = f"Версия {rel.version}" + (" — установлена" if rel.version == __version__ else "")
        sec = ui.section(title, [ui.text("•  " + item) for item in rel.items], hint=rel.date,
                         collapsible=i >= OPEN, key=f"changelog.{rel.version}")
        blocks.append(sec)
    return ui.page("Что нового", blocks, f"Установлена версия {__version__}")
