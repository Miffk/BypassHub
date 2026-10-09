"""Настройки самого менеджера (settings.json).

Настройки zapret (Game Filter, IPSet, фейки, пользовательские списки) хранятся
в файлах папки zapret — так же, как их хранит service.bat, — а настройки
tg-ws-proxy в его собственном config.json. Здесь только то, что относится к
менеджеру: выбранная стратегия, режим запуска, обновления, автозапуск.
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from .hotkeys import DEFAULT_BINDINGS
from .log import log

DEFAULTS: Dict[str, Any] = {
    "zapret": {
        "enabled": False,          # состояние переключателя (восстанавливается при запуске)
        "strategy": "general.bat",
        "mode": "process",         # process — winws.exe запускается программой; service — служба Windows
        "installed_version": "",
        "learn_apps": False,       # экспериментально: исключать IP, к которым подключаются выбранные программы
    },
    "tg": {
        "enabled": False,
        "asset": "auto",           # какой exe скачивать из релиза; auto — по системе
        "installed_version": "",
    },
    "updates": {
        "check_on_start": True,
        "interval_hours": 6,       # 0 — только при запуске
        "auto_install": True,
        "self_update": True,       # проверять обновления самого BypassHub
        "last_check": 0,
    },
    "app": {
        "autostart": False,
        "start_minimized": False,
        "close_to_tray": True,
        "restore_state": True,     # при запуске включать то, что было включено
        "stop_on_exit": True,      # выключать zapret (режим process) и прокси при выходе
    },
    "hotkeys": {
        "enabled": True,
        "bindings": dict(DEFAULT_BINDINGS),  # действие → "Ctrl+Alt+Z"; пустая строка — без сочетания
    },
    "appearance": {
        "mode": "dark",            # dark | light | system
        "preset": "Океан",         # имя набора цветов или "custom"
        "colors": ["#2b7fff", "#00c6ff"],  # 1–3 акцентных цвета; несколько — градиент
        "glass": False,            # прозрачный фон окна
        "opacity": 80,             # непрозрачность фона, %
        "blur": True,              # размытие того, что под окном
        "blur_level": 70,          # < 50 — мягкое (Aero), ≥ 50 — сильное (Acrylic)
    },
}


def _merge(defaults: dict, data: dict) -> dict:
    out = deepcopy(defaults)
    for key, value in data.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = _merge(DEFAULTS, raw)
            except Exception as exc:
                log.warning("Не удалось прочитать %s: %r — используются значения по умолчанию",
                            self.path, exc)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, section: str, key: str) -> Any:
        with self._lock:
            return self.data.get(section, {}).get(key, DEFAULTS[section][key])

    def set(self, section: str, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            self.data.setdefault(section, {})[key] = value
            if save:
                self.save()
