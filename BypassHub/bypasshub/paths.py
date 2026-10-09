"""Расположение данных программы.

По умолчанию всё хранится в C:\\ProgramData\\BypassHub: путь без кириллицы и
вне OneDrive (zapret плохо работает из таких папок), а программа всё равно
запускается с правами администратора.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME


def _default_root() -> Path:
    override = os.environ.get("BYPASSHUB_DATA")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


class Paths:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _default_root()
        self.settings = self.root / "settings.json"
        self.logs = self.root / "logs"
        self.log_file = self.logs / "bypasshub.log"
        self.winws_log = self.logs / "winws.log"
        self.downloads = self.root / "downloads"
        self.zapret = self.root / "zapret"
        self.tgproxy = self.root / "tgwsproxy"
        self.show_flag = self.root / "show.flag"

    def ensure(self) -> None:
        for p in (self.root, self.logs, self.downloads):
            p.mkdir(parents=True, exist_ok=True)


def resource_path(name: str) -> Path:
    """Файл, упакованный внутрь exe (PyInstaller) или лежащий рядом с исходниками."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent.parent / name
