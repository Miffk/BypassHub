from __future__ import annotations

import logging
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, List

log = logging.getLogger("bypasshub")

_FMT = "%(asctime)s  %(levelname)-5s  %(message)s"


class MemoryHandler(logging.Handler):
    """Хранит последние строки лога для вкладки «Журнал» и уведомляет подписчиков."""

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.lines: deque = deque(maxlen=capacity)
        self._listeners: List[Callable[[str], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[str], None]) -> Callable[[], None]:
        """Подписка на новые строки; возвращает функцию отписки."""
        with self._lock:
            self._listeners.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)
        return unsubscribe

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "no_ui", False):
            return  # ошибки самого интерфейса не показываем в интерфейсе — иначе возможна петля
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self.lines.append(line)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(line)
            except Exception:
                pass


memory_handler = MemoryHandler()


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(_FMT, datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=1, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)

    memory_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    memory_handler.setLevel(logging.INFO)
    log.addHandler(memory_handler)
