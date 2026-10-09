"""Оформление: светлая/тёмная палитра, акцентные цвета (градиент) и отрисовка
градиентных элементов через Pillow."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

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

SCALE = 2  # картинки рисуются с запасом для чётких краёв на HiDPI


@dataclass
class Palette:
    mode: str                      # "dark" | "light"
    accents: List[str]
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    on_accent: str = "#ffffff"
    glass_key: str = ""            # цвет-ключ, который становится прозрачным в режиме стекла
    glass: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def accent(self) -> str:
        return self.accents[0]

    @property
    def window_bg(self) -> str:
        return self.glass_key if self.glass else self.bg


DARK = dict(bg="#0f1115", surface="#171a21", surface2="#20242d", border="#2a2f3a",
            text="#e9ebf1", muted="#8b93a7", glass_key="#0f1116")
LIGHT = dict(bg="#f2f4f8", surface="#ffffff", surface2="#eceff5", border="#dfe3eb",
             text="#151821", muted="#687085", glass_key="#f2f4f9")


def system_mode() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        except OSError:
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
    base = DARK if mode == "dark" else LIGHT
    return Palette(mode=mode, accents=normalize_colors(appearance.get("colors")),
                   glass=bool(appearance.get("glass")), **base)


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


def apply_ctk_theme(p: Palette) -> None:
    """Подменяет цвета встроенной темы customtkinter, чтобы все стандартные
    виджеты выглядели в едином минималистичном стиле."""
    ctk.set_appearance_mode(p.mode)
    t = ctk.ThemeManager.theme
    two = lambda c: [c, c]  # noqa: E731
    acc = p.accent
    acc_hover = mix(acc, "#000000" if p.mode == "light" else "#ffffff", 0.15)
    t["CTk"]["fg_color"] = two(p.window_bg)
    t["CTkToplevel"]["fg_color"] = two(p.bg)
    t["CTkFrame"].update(fg_color=two(p.surface), top_fg_color=two(p.surface2), border_color=two(p.border))
    t["CTkButton"].update(fg_color=two(acc), hover_color=two(acc_hover), border_color=two(p.border),
                          text_color=two(readable_on([acc])), text_color_disabled=two(p.muted))
    t["CTkLabel"]["text_color"] = two(p.text)
    t["CTkEntry"].update(fg_color=two(p.surface2), border_color=two(p.border), text_color=two(p.text),
                         placeholder_text_color=two(p.muted))
    t["CTkTextbox"].update(fg_color=two(p.surface2), border_color=two(p.border), text_color=two(p.text),
                           scrollbar_button_color=two(p.border), scrollbar_button_hover_color=two(p.muted))
    knob = "#ffffff" if p.mode == "dark" else mix(p.text, "#ffffff", 0.3)
    t["CTkSwitch"].update(fg_color=two(p.border), progress_color=two(acc), button_color=two(knob),
                          button_hover_color=two(mix(knob, p.muted, 0.3)), text_color=two(p.text))
    t["CTkCheckBox"].update(fg_color=two(acc), hover_color=two(acc_hover), border_color=two(p.muted),
                            text_color=two(p.text))
    t["CTkSlider"].update(fg_color=two(p.border), progress_color=two(acc), button_color=two(acc),
                          button_hover_color=two(acc_hover))
    t["CTkProgressBar"].update(fg_color=two(p.border), progress_color=two(acc))
    t["CTkSegmentedButton"].update(fg_color=two(p.surface2), selected_color=two(acc),
                                   selected_hover_color=two(acc_hover), unselected_color=two(p.surface2),
                                   unselected_hover_color=two(p.border), text_color=two(p.text))
    t["CTkComboBox"].update(fg_color=two(p.surface2), border_color=two(p.border), button_color=two(p.border),
                            button_hover_color=two(p.muted), text_color=two(p.text))
    t["CTkOptionMenu"].update(fg_color=two(p.surface2), button_color=two(p.border),
                              button_hover_color=two(p.muted), text_color=two(p.text))
    t["DropdownMenu"].update(fg_color=two(p.surface), hover_color=two(p.surface2), text_color=two(p.text))
    t["CTkScrollbar"].update(button_color=two(p.border), button_hover_color=two(p.muted))
    t["CTkScrollableFrame"]["label_fg_color"] = two(p.surface2)
    if sys.platform == "win32":
        t["CTkFont"]["family"] = "Segoe UI"


# ---------------------------------------------------------------- шрифты и иконки

def _font_candidates(bold: bool) -> List[str]:
    if sys.platform == "win32":
        fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        names = ["seguisb.ttf", "segoeuib.ttf"] if bold else ["segoeui.ttf"]
        return [os.path.join(fonts, n) for n in names] + ["arial.ttf"]
    base = "/usr/share/fonts/truetype/dejavu/"
    return [base + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")]


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in _font_candidates(bold):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


ICONS = {  # глифы Segoe MDL2 Assets / Segoe Fluent Icons (есть в Windows 10/11)
    "home": "\uE80F", "shield": "\uE83D", "send": "\uE724", "sync": "\uE895",
    "settings": "\uE713", "list": "\uE8A5", "power": "\uE7E8",
}


@lru_cache(maxsize=8)
def icon_font(size: int) -> Optional[ImageFont.ImageFont]:
    if sys.platform != "win32":
        return None
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            return ImageFont.truetype(os.path.join(fonts, name), size)
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- градиенты

def gradient(size: Tuple[int, int], colors: Sequence[str], angle: str = "diag") -> Image.Image:
    w, h = size
    cols = [hex_to_rgb(c) for c in colors] or [(43, 127, 255)]
    if len(cols) == 1:
        return Image.new("RGBA", size, cols[0] + (255,))
    # одномерная полоса нужной длины, затем растягивание
    length = w + h if angle == "diag" else (w if angle == "h" else h)
    strip = Image.new("RGBA", (max(length, 2), 1))
    px = strip.load()
    segs = len(cols) - 1
    for x in range(strip.width):
        t = x / (strip.width - 1)
        i = min(int(t * segs), segs - 1)
        lt = t * segs - i
        a, b = cols[i], cols[i + 1]
        px[x, 0] = tuple(int(a[k] + (b[k] - a[k]) * lt) for k in range(3)) + (255,)
    if angle == "h":
        return strip.resize((w, h))
    if angle == "v":
        return strip.resize((h, 1)).rotate(-90, expand=True).resize((w, h))
    # диагональ: строка y — это участок полосы, сдвинутый на y
    big = Image.new("RGBA", (w, h))
    for y in range(h):
        big.paste(strip.crop((y, 0, y + w, 1)), (0, y))
    return big


@lru_cache(maxsize=256)
def _gradient_cached(size, colors, angle):
    return gradient(size, colors, angle)


def rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=radius, fill=255)
    out = img.copy()
    out.putalpha(mask)
    return out


def pill(size: Tuple[int, int], colors: Sequence[str], radius: int, angle: str = "h") -> Image.Image:
    w, h = size
    g = _gradient_cached((w * SCALE, h * SCALE), tuple(colors), angle).copy()
    return rounded(g, radius * SCALE)


def draw_text(img: Image.Image, xy, text: str, size: int, color: str, bold: bool = False,
              anchor: str = "lm") -> None:
    ImageDraw.Draw(img).text((xy[0] * SCALE, xy[1] * SCALE), text, font=font(size * SCALE, bold),
                             fill=color, anchor=anchor)


def draw_icon(img: Image.Image, xy, name: str, size: int, color: str) -> bool:
    f = icon_font(size * SCALE)
    if f is None or name not in ICONS:
        return False
    ImageDraw.Draw(img).text((xy[0] * SCALE, xy[1] * SCALE), ICONS[name], font=f, fill=color, anchor="mm")
    return True


def gradient_text(text: str, size: int, colors: Sequence[str], bold: bool = True) -> Image.Image:
    f = font(size * SCALE, bold)
    bbox = f.getbbox(text)
    w, h = bbox[2] + 4, bbox[3] + 6
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=f, fill=255)
    g = gradient((w, h), colors, "h")
    g.putalpha(mask)
    return g


def to_ctk(img: Image.Image) -> ctk.CTkImage:
    return ctk.CTkImage(light_image=img, dark_image=img, size=(img.width // SCALE, img.height // SCALE))
