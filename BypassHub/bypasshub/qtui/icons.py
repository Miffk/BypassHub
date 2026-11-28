"""Значки интерфейса: тонкие линейные пиктограммы в духе SF Symbols (свои, рисуются из SVG).

Одинаково выглядят в Windows и Linux (не зависят от шрифтов значков системы) и перекрашиваются
в любой цвет темы."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap

# 24×24, линии толщиной 1.8, скруглённые концы; цвет подставляется вместо currentColor
_PATHS = {
    "home": '<path d="M3.5 10.5 12 3.8l8.5 6.7V19a1.6 1.6 0 0 1-1.6 1.6H15v-5.4a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v5.4'
            'H5.1A1.6 1.6 0 0 1 3.5 19z"/>',
    "shield": '<path d="M12 3.2 19 6v5.3c0 4.4-2.9 8-7 9.5-4.1-1.5-7-5.1-7-9.5V6z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>',
    "send": '<path d="M20.5 3.5 3.6 10.3l6.6 2.6 2.6 6.6z"/><path d="m20.5 3.5-10.3 9.4"/>',
    "vpn": '<circle cx="12" cy="12" r="8.6"/><path d="M3.4 12h17.2"/>'
           '<path d="M12 3.4c2.4 2.4 3.6 5.3 3.6 8.6s-1.2 6.2-3.6 8.6c-2.4-2.4-3.6-5.3-3.6-8.6S9.6 5.8 12 3.4z"/>',
    "sync": '<path d="M19.6 10.2A7.8 7.8 0 0 0 5.4 7.6"/><path d="M4.6 4.2v3.9h3.9"/>'
            '<path d="M4.4 13.8a7.8 7.8 0 0 0 14.2 2.6"/><path d="M19.4 19.8v-3.9h-3.9"/>',
    "news": '<path d="M11 3.5 12.8 8.2 17.5 10l-4.7 1.8L11 16.5l-1.8-4.7L4.5 10l4.7-1.8z"/>'
            '<path d="m18 14.5.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
    "settings": '<path d="M4 6.5h9"/><path d="M17.5 6.5H20"/><circle cx="15.3" cy="6.5" r="2.2"/>'
                '<path d="M4 12h2.5"/><path d="M11 12h9"/><circle cx="8.7" cy="12" r="2.2"/>'
                '<path d="M4 17.5h11"/><path d="M19.5 17.5H20"/><circle cx="17.3" cy="17.5" r="2.2"/>',
    "list": '<path d="M9 6.5h11"/><path d="M9 12h11"/><path d="M9 17.5h11"/>'
            '<circle cx="4.8" cy="6.5" r="1" fill="currentColor"/><circle cx="4.8" cy="12" r="1" fill="currentColor"/>'
            '<circle cx="4.8" cy="17.5" r="1" fill="currentColor"/>',
    "heart": '<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>',
    "power": '<path d="M12 3.5v8"/><path d="M6.6 6.8a7.6 7.6 0 1 0 10.8 0"/>',
    "bolt": '<path d="M13 3 5.5 13.2h5.6L10.5 21 18.5 10.6h-5.7z"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2.4"/><path d="M8.3 10.5V8a3.7 3.7 0 0 1 7.4 0v2.5"/>',
}


def _svg(name: str, color: str, weight: float) -> bytes:
    body = _PATHS.get(name, "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{weight}" stroke-linecap="round" stroke-linejoin="round">'
            + body.replace("currentColor", color) + "</svg>").encode("utf-8")


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int, dpr: float = 2.0, weight: float = 1.8) -> QPixmap:
    """Значок нужного цвета и размера (в логических пикселях; dpr — для чётких экранов)."""
    from PySide6.QtSvg import QSvgRenderer
    side = max(1, round(size * dpr))
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color, weight)))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, side, side))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def paint(p: QPainter, name: str, rect: QRectF, color: str, weight: float = 1.8) -> None:
    """Нарисовать значок в прямоугольнике (на плитках и кнопках)."""
    from PySide6.QtSvg import QSvgRenderer
    QSvgRenderer(QByteArray(_svg(name, color, weight))).render(p, rect)


def names() -> list:
    return list(_PATHS)
