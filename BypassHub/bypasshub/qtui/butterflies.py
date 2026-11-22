"""Бабочки в левом меню: анимация (APNG из assets) на свободном месте под пунктами.

Не мешает работе: щелчки проходят сквозь неё, кадры декодируются в фоне перед каждым показом
и освобождаются после (в памяти ~30 МБ только пока бабочка летит)."""
from __future__ import annotations

import threading
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..log import log
from ..paths import resource_path

ASSET = "assets/butterflies.png"
Frame = Optional[Tuple[int, int, QImage]]  # (x, y, картинка) в масштабе показа; None — пустой кадр


def _edge_fade(width: int, height: int, side: float = 0.38, ends: float = 0.2):
    """Маска прозрачности: к краям кадра изображение плавно растворяется (по бокам — шире, вверху и внизу —
    уже), чтобы у дымки вокруг бабочки не было видимых краёв."""
    from PIL import Image, ImageChops

    def ramp(n: int, part: float) -> bytes:
        k = max(1.0, n * part)
        out = []
        for i in range(n):
            t = min(1.0, min(i, n - 1 - i) / k)
            out.append(int(255 * t * t * (3 - 2 * t)))  # плавная S-кривая, без заметной границы
        return bytes(out)
    col = Image.frombytes("L", (width, 1), ramp(width, side)).resize((width, height))
    row = Image.frombytes("L", (1, height), ramp(height, ends)).resize((width, height))
    return ImageChops.multiply(col, row)


def load_frames(scale: float, path=None) -> Tuple[Tuple[int, int], int, List[Frame]]:
    """Кадры APNG: каждый обрезан по видимой части и уменьшен до scale. → ((ширина, высота), мс на кадр, кадры)."""
    from PIL import Image, ImageChops
    im = Image.open(str(path or resource_path(ASSET)))
    width, height = im.size
    duration = int(round(float(im.info.get("duration") or 83)))
    fade = _edge_fade(width, height)
    frames: List[Frame] = []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        rgba = im.convert("RGBA")
        rgba.putalpha(ImageChops.multiply(rgba.getchannel("A"), fade))
        box = rgba.getbbox()
        if not box:
            frames.append(None)
            continue
        part = rgba.crop(box)
        w, h = max(1, round(part.width * scale)), max(1, round(part.height * scale))
        if (w, h) != part.size:
            part = part.resize((w, h), Image.BILINEAR)
        img = QImage(part.tobytes("raw", "RGBA"), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        frames.append((round(box[0] * scale), round(box[1] * scale), img))
    return (round(width * scale), round(height * scale)), max(20, duration), frames


class ButterflyArea(QWidget):
    """Пустое место в левом меню (между пунктами и «Поддержать проект»); play() — один пролёт бабочки.
    Пока бабочки нет, ничего не рисует, место просто растягивается."""

    def __init__(self, call_ui: Callable[[Callable[[], None]], None]):
        super().__init__()
        self.call_ui = call_ui
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(0)
        self.frames: List[Frame] = []
        self.canvas = (0, 0)
        self.index = -1
        self.loading = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next)

    @property
    def busy(self) -> bool:
        return self.loading or self.index >= 0

    def scale(self) -> float:
        """По высоте свободного места; по ширине — с небольшим запасом: края кадра всё равно гаснут."""
        return min(0.75, self.height() / 880, self.width() * 1.25 / 450)

    def play(self) -> None:
        if self.busy or not self.isVisible() or self.height() < 140:
            return
        self.loading = True
        scale = self.scale()

        def work():
            try:
                result = load_frames(scale)
            except Exception as exc:
                log.debug("Бабочки: %r", exc)
                result = None
            self.call_ui(lambda: self._start(result))
        threading.Thread(target=work, daemon=True, name="butterflies").start()

    def _start(self, result) -> None:
        self.loading = False
        if not result or not self.isVisible():
            return
        self.canvas, duration, self.frames = result
        self.index = 0
        self.timer.start(duration)
        self.update()

    def stop(self) -> None:
        self.timer.stop()
        self.index = -1
        self.frames = []
        self.update()

    def _next(self) -> None:
        self.index += 1
        if self.index >= len(self.frames):
            self.stop()
        else:
            self.update()

    def paintEvent(self, _event) -> None:
        if not (0 <= self.index < len(self.frames)) or self.frames[self.index] is None:
            return
        x, y, img = self.frames[self.index]
        cw, ch = self.canvas
        left = (self.width() - cw) // 2
        top = max(0, (self.height() - ch) // 2)
        p = QPainter(self)
        p.drawImage(QRect(left + x, top + y, img.width(), img.height()), img)
        p.end()
