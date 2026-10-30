"""Палитра оформления: светлая/тёмная база, окрашенная в акцентные цвета (градиент).
Без зависимостей от библиотеки интерфейса — общая для окна Qt и прежнего окна customtkinter."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Sequence, Tuple

PRESETS = {
    "Океан": ["#2b7fff", "#00c6ff"],
    "Аврора": ["#7f5af0", "#2cb67d"],
    "Закат": ["#ff5f6d", "#ffc371"],
    "Неон": ["#f72585", "#7209b7", "#4cc9f0"],
    "Мята": ["#11998e", "#38ef7d"],
    "Лава": ["#f12711", "#f5af19"],
    "Ночь": ["#4776e6", "#8e54e9"],
    "Графит": ["#8e9aaf", "#cbc0d3"],
}
DEFAULT_PRESET = "Океан"


@dataclass
class Palette:
    """Палитра: нейтральная база (тёмная/светлая), окрашенная в акцентные цвета.

    Фон страниц и боковой панели — вертикальный градиент по акцентным цветам
    (page_stops / side_stops); карточки — «полупрозрачная» подложка поверх
    градиента в своей точке (card_on)."""
    mode: str                      # "dark" | "light"
    accents: List[str]
    tint: float                    # насыщенность фона 0..1
    page_stops: List[str]
    side_stops: List[str]
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent_hover: str
    on_accent: str
    knob: str
    knob_hover: str

    @property
    def accent(self) -> str:
        return self.accents[0]

    def card_on(self, bg: str) -> str:
        """Цвет карточки поверх фона bg."""
        return mix(bg, "#ffffff", 0.055) if self.mode == "dark" else mix(bg, "#ffffff", 0.72)

    def roles(self) -> dict:
        """Именованные цвета — по ним интерфейс перекрашивается на лету."""
        return {k: getattr(self, k) for k in (
            "bg", "surface", "surface2", "border", "text", "muted", "accent_hover", "on_accent",
            "knob", "knob_hover")} | {"accent": self.accent}


DARK = dict(bg="#0f1115", text="#e9ebf1")
LIGHT = dict(bg="#f2f4f8", text="#151821")


def gradient_at(stops: Sequence[str], t: float) -> str:
    """Цвет градиента в точке t (0..1)."""
    if len(stops) == 1:
        return stops[0]
    t = max(0.0, min(1.0, t))
    segs = len(stops) - 1
    i = min(int(t * segs), segs - 1)
    return mix(stops[i], stops[i + 1], t * segs - i)


MAX_SAVED = 16
MY_PREFIX = "my:"


def preset_key_for(appearance: dict) -> str:
    """Ключ набора для текущих цветов: имя готового набора, «my:<имя>» своей темы или custom."""
    colors = normalize_colors(appearance.get("colors"))
    for name, cols in PRESETS.items():
        if cols == colors:
            return name
    for theme in appearance.get("saved") or []:
        if normalize_colors(theme.get("colors")) == colors:
            return MY_PREFIX + theme.get("name", "")
    return "custom"


def remember_theme(appearance: dict) -> str:
    """Сохранить текущие цвета в «Мои темы», если такой темы ещё нет.
    Возвращает ключ набора (см. preset_key_for)."""
    key = preset_key_for(appearance)
    if key != "custom":
        return key
    saved = appearance.setdefault("saved", [])
    used = {t.get("name") for t in saved}
    n = 1
    while f"Тема {n}" in used:
        n += 1
    name = f"Тема {n}"
    saved.append({"name": name, "colors": normalize_colors(appearance.get("colors"))})
    del saved[:-MAX_SAVED]  # самые старые уходят, если тем слишком много
    return MY_PREFIX + name


def system_mode() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    elif sys.platform.startswith("linux"):
        try:  # KDE Plasma и GNOME сообщают Qt, светлая тема или тёмная
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.instance() is not None:
                scheme = QGuiApplication.styleHints().colorScheme()
                if scheme == Qt.ColorScheme.Light:
                    return "light"
                if scheme == Qt.ColorScheme.Dark:
                    return "dark"
        except Exception:
            pass
    return "dark"


def normalize_colors(colors: Sequence[str]) -> List[str]:
    out = []
    for c in colors or []:
        c = str(c).strip()
        if len(c) == 7 and c.startswith("#"):
            try:
                int(c[1:], 16)
                out.append(c.lower())
            except ValueError:
                pass
    return out[:3] or list(PRESETS[DEFAULT_PRESET])


def make_palette(appearance: dict) -> Palette:
    mode = appearance.get("mode", "dark")
    if mode == "system":
        mode = system_mode()
    dark = mode == "dark"
    base = DARK if dark else LIGHT
    accents = normalize_colors(appearance.get("colors"))
    tint = max(0, min(100, int(appearance.get("tint", 70)))) / 100
    text = base["text"]
    # фон страниц: база, окрашенная в каждый из акцентных цветов
    page = [mix(base["bg"], c, (0.30 if dark else 0.40) * tint) for c in accents]
    # боковая панель — того же цвета, но глубже (тёмная тема) или светлее (светлая)
    side = [mix(c, "#000000", 0.30) if dark else mix(c, "#ffffff", 0.35) for c in page]
    bg = gradient_at(page, 0.5)
    p = Palette(mode=mode, accents=accents, tint=tint, page_stops=page, side_stops=side, bg=bg,
                surface="", surface2="", border="", text=text, muted="", accent_hover="", on_accent="",
                knob="", knob_hover="")
    p.surface = p.card_on(bg)
    p.surface2 = mix(p.surface, "#ffffff", 0.06) if dark else mix(p.surface, "#1b2440", 0.05)
    p.border = mix(p.surface, text, 0.16 if dark else 0.13)
    p.muted = mix(text, bg, 0.42)
    p.accent_hover = mix(p.accent, "#ffffff" if dark else "#000000", 0.15)
    p.on_accent = readable_on([p.accent])
    # ползунок переключателя: почти белый в тёмной теме (не #ffffff, чтобы не совпасть
    # с цветом текста на кнопках при перекраске) и тёмно-серый в светлой
    p.knob = "#f3f4f6" if dark else mix(text, "#ffffff", 0.3)
    p.knob_hover = mix(p.knob, p.muted, 0.3)
    return p


# ---------------------------------------------------------------- цвета

def hex_to_rgb(c: str) -> Tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb[:3])


def mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def readable_on(colors: Sequence[str]) -> str:
    """Белый или почти чёрный текст поверх градиента — что контрастнее."""
    r, g, b = [sum(hex_to_rgb(c)[i] for c in colors) / len(colors) for i in range(3)]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#111318" if lum > 170 else "#ffffff"
