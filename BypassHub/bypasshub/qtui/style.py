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

# системные цвета Apple (тёмная / светлая тема)
GREEN = {"dark": "#30d158", "light": "#248a3d"}
YELLOW = {"dark": "#ffd60a", "light": "#b25000"}
RED = {"dark": "#ff453a", "light": "#d70015"}

# глифы Segoe Fluent Icons / Segoe MDL2 Assets (есть в Windows 10/11)
ICONS = {
    "home": "", "shield": "", "send": "", "sync": "",
    "settings": "", "list": "", "power": "", "vpn": "", "heart": "",
    "news": "",
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
    """Подложка карточки поверх фона: (цвет, непрозрачность) — «стекло» в духе Apple."""
    return ("#ffffff", 0.05) if P.mode == "dark" else ("#ffffff", 0.82)


def surface2_alpha() -> Tuple[str, float]:
    """Заливка кнопок, полей и строк внутри карточек — системная «fill» Apple."""
    return ("#76768a", 0.22) if P.mode == "dark" else ("#76768a", 0.11)


def hairline() -> str:
    """Тонкая граница карточек и разделители."""
    return rgba("#ffffff", 0.08) if P.mode == "dark" else rgba("#000000", 0.07)


def hairline_qcolor() -> QColor:
    return qcolor("#ffffff", 0.07) if P.mode == "dark" else qcolor("#000000", 0.08)


_fonts_loaded = False
UI_FONT = "Inter"
DISPLAY_FONT = "Inter Display"


def load_fonts() -> None:
    """Шрифт Inter (близок к San Francisco) — из assets/fonts; одинаковый в Windows и Linux."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    from PySide6.QtGui import QFontDatabase
    from ..paths import resource_path
    folder = resource_path("assets/fonts")
    for f in sorted(folder.glob("*.ttf")) if folder.is_dir() else []:
        QFontDatabase.addApplicationFont(str(f))


def _has_family(name: str) -> bool:
    from PySide6.QtGui import QFontDatabase
    return name in QFontDatabase.families()


def ui_font_family() -> str:
    if _has_family(UI_FONT):
        return UI_FONT
    return "Segoe UI Variable Text" if sys.platform == "win32" else "Noto Sans"


def display_font_family() -> str:
    """Для крупных заголовков: Inter Display (плотнее, как SF Pro Display)."""
    return DISPLAY_FONT if _has_family(DISPLAY_FONT) else ui_font_family()


def icon_font_family() -> str:
    return "Segoe Fluent Icons" if sys.platform == "win32" else ""


def base_font() -> QFont:
    load_fonts()
    f = QFont(ui_font_family())
    f.setPointSizeF(9.75)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
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
    fill_c, fill_a = surface2_alpha()
    fill = rgba(fill_c, fill_a)
    fill_hover = rgba(fill_c, fill_a * 1.55)
    fill_press = rgba(fill_c, fill_a * 2.1)
    line = hairline()
    accent = p.accent
    accent_hover = PL.mix(accent, "#ffffff", 0.12)
    accent_press = PL.mix(accent, "#000000", 0.12)
    on_accent = PL.readable_on([accent])
    menu_bg = "#232327" if dark else "#fdfdfe"
    seg_on = "#5b5b63" if dark else "#ffffff"
    check = _check_image(on_accent)
    chevron = _chevron_image(muted)
    disp = display_font_family()
    return f"""
* {{ color: {text}; }}
QMainWindow, QDialog {{ background: {p.bg}; }}
QToolTip {{ background: {menu_bg}; color: {text}; border: 1px solid {line}; border-radius: 8px; padding: 6px 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-family: "{disp}"; font-size: 23pt; font-weight: 700; }}
QLabel[role="h1"] {{ font-family: "{disp}"; font-size: 16pt; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {muted}; }}
QLabel[role="small"] {{ color: {muted}; font-size: 8.5pt; }}
QLabel[role="status"] {{ font-size: 10.5pt; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: "JetBrains Mono", Consolas, monospace; }}
QLabel[role="help"] {{ color: {muted}; background: {fill}; border-radius: 9px; font-size: 8pt; font-weight: 700; }}
QLabel[role="help"]:hover {{ color: {on_accent}; background: {accent}; }}
QLabel[role="group"] {{ color: {muted}; font-size: 8.5pt; font-weight: 600; letter-spacing: 0.4px; }}
QLabel[role="brand"] {{ font-family: "{disp}"; font-size: 13.5pt; font-weight: 700; }}
QFrame#card {{ background: {rgba(card_c, card_a)}; border: 1px solid {line}; border-radius: 18px; }}
QFrame#row {{ background: {fill}; border: 1px solid transparent; border-radius: 12px; }}
QFrame#row:hover {{ background: {fill_hover}; }}
QFrame#row[active="true"] {{ background: {rgba(accent, 0.16 if dark else 0.10)}; border: 1px solid {rgba(accent, 0.75)}; }}
QFrame#banner {{ background: {rgba(accent, 0.12)}; border: 1px solid {rgba(accent, 0.45)}; border-radius: 16px; }}
QFrame#warn {{ background: {rgba("#ffd60a", 0.12)}; border: 1px solid {rgba("#ffd60a", 0.45)}; border-radius: 12px; }}
QFrame#segbar {{ background: {fill}; border-radius: 10px; }}
QFrame#separator {{ background: {line}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{ background: {fill}; border: none; border-radius: 16px; padding: 7px 16px; min-height: 18px;
              font-weight: 500; }}
QPushButton:hover {{ background: {fill_hover}; }}
QPushButton:pressed {{ background: {fill_press}; }}
QPushButton:disabled {{ color: {rgba(text, 0.3)}; background: {rgba(fill_c, fill_a * 0.6)}; }}
QPushButton#primary {{ color: {on_accent}; background: {accent}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {accent_hover}; }}
QPushButton#primary:pressed {{ background: {accent_press}; }}
QPushButton#primary:disabled {{ background: {fill}; color: {rgba(text, 0.3)}; }}
QPushButton#flat {{ background: transparent; padding: 4px 8px; color: {muted}; border-radius: 8px; }}
QPushButton#flat:hover {{ background: {fill}; color: {text}; }}
QPushButton#nav {{ background: transparent; text-align: left; padding: 0; border-radius: 9px; min-height: 36px; }}
QPushButton#nav:hover {{ background: {rgba(text, 0.06)}; }}
QPushButton#nav:checked {{ background: {accent}; }}
QPushButton#seg {{ background: transparent; border-radius: 8px; padding: 6px 10px; color: {text}; font-weight: 500; }}
QPushButton#seg:hover {{ background: {rgba(text, 0.05)}; }}
QPushButton#seg:checked {{ background: {seg_on}; color: {text}; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{ background: {fill}; border: 1px solid transparent; border-radius: 10px;
    padding: 7px 10px; selection-background-color: {accent}; selection-color: {on_accent}; }}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{ background: {fill_hover}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; background: {fill}; }}
QPlainTextEdit[role="mono"], QLineEdit[role="mono"] {{ font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 9.5pt; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {menu_bg}; border: 1px solid {line}; border-radius: 10px; padding: 4px;
    outline: none; selection-background-color: {accent}; selection-color: {on_accent}; }}

QCheckBox {{ spacing: 9px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1.5px solid {rgba(text, 0.35)};
    background: {rgba(fill_c, fill_a * 0.5)}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url({check}); }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 8px 2px 8px 2px; }}
QScrollBar::handle:vertical {{ background: {rgba(text, 0.18)}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {rgba(text, 0.34)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QScrollBar:horizontal {{ height: 0; }}

QProgressBar {{ background: {fill}; border: none; border-radius: 3px; max-height: 6px; text-align: center;
    color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
QSlider {{ min-height: 26px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {fill}; border-radius: 2px; margin: 0 9px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; margin-left: 9px; }}
QSlider::add-page:horizontal {{ background: {fill}; border-radius: 2px; margin-right: 9px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 18px; height: 18px; margin: -7px -9px;
    border-radius: 9px; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 6px 12px; margin-right: 4px; border-radius: 8px; color: {muted}; }}
QTabBar::tab:selected {{ background: {fill}; color: {text}; }}
QTabBar::tab:hover {{ color: {text}; }}
QMenu {{ background: {menu_bg}; border: 1px solid {line}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent}; color: {on_accent}; }}
QMessageBox QLabel {{ font-size: 10pt; }}
"""
