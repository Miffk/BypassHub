"""Бабочки на главном экране: анимация (APNG из assets) поверх страницы.

Не мешает работе: щелчки проходят сквозь неё, кадры декодируются в фоне перед каждым показом
и освобождаются после (в памяти ~30 МБ только пока бабочка летит)."""
from __future__ import annotations

import threading
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from ..log import log
from ..paths import resource_path

ASSET = "assets/butterflies.png"
Frame = Optional[Tuple[int, int, QImage]]  # (x, y, картинка) в масштабе показа; None — пустой кадр


def _edge_fade(width: int, height: int, part: float = 0.14):
    """Маска прозрачности: к краям кадра изображение плавно гаснет — у свечения нет видимой рамки."""
    from PIL import Image
    def ramp(n: int) -> List[int]:
        k = max(1, int(min(width, height) * part))
        return [min(255, int(255 * min(i, n - 1 - i) / k)) for i in range(n)]
    cols, rows = ramp(width), ramp(height)
    col = Image.frombytes("L", (width, 1), bytes(cols)).resize((width, height))
    row = Image.frombytes("L", (1, height), bytes(rows)).resize((width, height))
    from PIL import ImageChops
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


class ButterflyOverlay(QWidget):
    """Прозрачный слой поверх страницы; play() — один пролёт бабочки у правого края."""

    MARGIN = 12

    def __init__(self, host: QWidget, call_ui: Callable[[Callable[[], None]], None]):
        super().__init__(host)
        self.host = host
        self.call_ui = call_ui
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.frames: List[Frame] = []
        self.canvas = (0, 0)
        self.index = -1
        self.loading = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next)
        self.hide()

    @property
    def busy(self) -> bool:
        return self.loading or self.index >= 0

    def play(self) -> None:
        if self.busy or self.host.height() < 200:
            return
        self.loading = True
        scale = max(0.4, min(0.75, self.host.height() * 0.85 / 880))

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
        if not result or not self.host.isVisible():
            return
        self.canvas, duration, self.frames = result
        self.index = 0
        self.setGeometry(self.host.rect())
        self.raise_()
        self.show()
        self.timer.start(duration)
        self.update()

    def stop(self) -> None:
        self.timer.stop()
        self.index = -1
        self.frames = []
        self.hide()

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
        left = max(0, self.width() - cw - self.MARGIN)
        top = max(0, (self.height() - ch) // 2)
        p = QPainter(self)
        p.drawImage(QRect(left + x, top + y, img.width(), img.height()), img)
        p.end()
