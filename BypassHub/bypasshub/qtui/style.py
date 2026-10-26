"""Оформление окна Qt: таблица стилей из палитры (bypasshub.palette).

Смена темы — одна таблица стилей для всего приложения, Qt сам перерисовывает всё окно.
Карточки по-настоящему полупрозрачные — фон-градиент просвечивает сквозь них.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Tuple

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from .. import palette as PL
from ..palette import Palette

GREEN = {"dark": "#3ddc84", "light": "#16a34a"}
YELLOW = {"dark": "#f6c343", "light": "#c27c0e"}
RED = {"dark": "#ff6b6b", "light": "#dc2626"}

# глифы Segoe Fluent Icons / Segoe MDL2 Assets (есть в Windows 10/11)
ICONS = {
    "home": "", "shield": "", "send": "", "sync": "",
    "settings": "", "list": "", "power": "", "vpn": "", "heart": "",
}

P: Palette = PL.make_palette({"mode": "dark"})


def set_palette(p: Palette) -> None:
    global P
    P = p


def green() -> str:
    return GREEN[P.mode]


def yellow() -> str:
    return YELLOW[P.mode]


def red() -> str:
    return RED[P.mode]


def rgba(color: str, alpha: float) -> str:
    r, g, b = PL.hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {round(alpha * 255)})"


def qcolor(color: str, alpha: float = 1.0) -> QColor:
    c = QColor(color)
    c.setAlphaF(alpha)
    return c


def card_alpha() -> Tuple[str, float]:
    """Подложка карточки поверх градиента: (цвет, непрозрачность) — как Palette.card_on."""
    return ("#ffffff", 0.055) if P.mode == "dark" else ("#ffffff", 0.72)


def surface2_alpha() -> Tuple[str, float]:
    """Подложка строк и полей внутри карточек."""
    return ("#ffffff", 0.07) if P.mode == "dark" else ("#1b2440", 0.05)


def ui_font_family() -> str:
    return "Segoe UI Variable Text" if sys.platform == "win32" else "Noto Sans"


def icon_font_family() -> str:
    return "Segoe Fluent Icons" if sys.platform == "win32" else ""


def base_font() -> QFont:
    f = QFont(ui_font_family())
    f.setPointSizeF(10)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def gradient(colors, horizontal: bool = True) -> str:
    """qlineargradient из акцентных цветов."""
    if len(colors) == 1:
        colors = [colors[0], colors[0]]
    stops = ", ".join(f"stop:{i / (len(colors) - 1):.3f} {c}" for i, c in enumerate(colors))
    x2, y2 = (1, 0) if horizontal else (0, 1)
    return f"qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, {stops})"


_check_dir = Path(tempfile.gettempdir()) / "bypasshub-qt"


def _check_image(color: str) -> str:
    """Галочка для флажков (в таблице стилей её можно задать только картинкой)."""
    _check_dir.mkdir(parents=True, exist_ok=True)
    path = _check_dir / f"check-{color.lstrip('#')}.png"
    if not path.exists():
        img = QImage(36, 36, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(color), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(8, 19), QPointF(15, 26), QPointF(28, 11)])
        p.end()
        img.save(str(path))
    return path.as_posix()


def _chevron_image(color: str) -> str:
    """Стрелка выпадающего списка."""
    _check_dir.mkdir(parents=True, exist_ok=True)
    path = _check_dir / f"chevron-{color.lstrip('#')}.png"
    if not path.exists():
        img = QImage(24, 24, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(5, 9), QPointF(12, 16), QPointF(19, 9)])
        p.end()
        img.save(str(path))
    return path.as_posix()


def stylesheet(p: Palette) -> str:
    dark = p.mode == "dark"
    text, muted = p.text, p.muted
    card_c, card_a = card_alpha()
    s2_c, s2_a = surface2_alpha()
    surf2 = rgba(s2_c, s2_a)
    surf2_hover = rgba(s2_c, s2_a * 1.8)
    border = rgba(text, 0.16 if dark else 0.14)
    menu_bg = PL.mix(p.bg, "#ffffff", 0.07) if dark else PL.mix(p.bg, "#ffffff", 0.85)
    accent_grad = gradient(p.accents)
    accent_grad_hover = gradient([PL.mix(c, "#ffffff", 0.12) for c in p.accents])
    on_accent = PL.readable_on(p.accents)
    check = _check_image(on_accent)
    chevron = _chevron_image(muted)
    return f"""
* {{ color: {text}; }}
QMainWindow, QDialog {{ background: {p.bg}; }}
QToolTip {{ background: {menu_bg}; color: {text}; border: 1px solid {border}; padding: 4px 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 20pt; font-weight: 700; }}
QLabel[role="h1"] {{ font-size: 16pt; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 11.5pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {muted}; }}
QLabel[role="small"] {{ color: {muted}; font-size: 8.5pt; }}
QLabel[role="status"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: Consolas, monospace; }}
QFrame#card {{ background: {rgba(card_c, card_a)}; border-radius: 16px; }}
QFrame#row {{ background: {surf2}; border-radius: 12px; }}
QFrame#row[active="true"] {{ border: 2px solid {p.accent}; }}
QFrame#banner {{ background: {rgba(card_c, card_a)}; border: 1px solid {p.accent}; border-radius: 14px; }}
QFrame#warn {{ background: {rgba("#f6c343", 0.14)}; border: 1px solid {rgba("#f6c343", 0.5)}; border-radius: 10px; }}
QFrame#segbar {{ background: {surf2}; border-radius: 10px; }}

QPushButton {{ background: transparent; border: 1px solid {border}; border-radius: 10px; padding: 7px 16px;
              min-height: 20px; }}
QPushButton:hover {{ background: {surf2_hover}; }}
QPushButton:pressed {{ background: {surf2}; }}
QPushButton:disabled {{ color: {muted}; border-color: {rgba(text, 0.08)}; }}
QPushButton#primary {{ border: none; color: {on_accent}; background: {accent_grad}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {accent_grad_hover}; }}
QPushButton#primary:disabled {{ background: {surf2}; color: {muted}; }}
QPushButton#flat {{ border: none; padding: 4px 8px; color: {muted}; }}
QPushButton#flat:hover {{ background: {surf2_hover}; color: {text}; }}
QPushButton#nav {{ border: none; text-align: left; padding: 9px 12px; border-radius: 12px; color: {muted};
                  font-size: 10pt; }}
QPushButton#nav:hover {{ background: {surf2_hover}; color: {text}; }}
QPushButton#nav:checked {{ background: {accent_grad}; color: {on_accent}; font-weight: 600; }}
QPushButton#seg {{ border: none; border-radius: 8px; padding: 6px 10px; color: {text}; }}
QPushButton#seg:hover {{ background: {surf2_hover}; }}
QPushButton#seg:checked {{ background: {p.accent}; color: {PL.readable_on([p.accent])}; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{ background: {surf2}; border: 1px solid {border}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {p.accent}; selection-color: {on_accent}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {p.accent}; }}
QPlainTextEdit[role="mono"], QLineEdit[role="mono"] {{ font-family: Consolas, monospace; font-size: 9.5pt; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {menu_bg}; border: 1px solid {border}; padding: 4px; outline: none;
    selection-background-color: {p.accent}; selection-color: {on_accent}; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 2px solid {muted}; background: transparent; }}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url({check}); }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 6px 3px 6px 3px; }}
QScrollBar::handle:vertical {{ background: {rgba(text, 0.22)}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {rgba(text, 0.38)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QScrollBar:horizontal {{ height: 0; }}

QProgressBar {{ background: {surf2}; border: none; border-radius: 4px; max-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {accent_grad}; border-radius: 4px; }}
QSlider::groove:horizontal {{ height: 6px; background: {surf2}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {p.knob}; width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 6px 12px; margin-right: 4px; border-radius: 8px; color: {muted}; }}
QTabBar::tab:selected {{ background: {surf2}; color: {text}; }}
QTabBar::tab:hover {{ color: {text}; }}
QMenu {{ background: {menu_bg}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.accent}; color: {PL.readable_on([p.accent])}; }}
QMessageBox QLabel {{ font-size: 10pt; }}
"""
