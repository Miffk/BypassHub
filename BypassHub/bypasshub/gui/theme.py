"""Оформление: светлая/тёмная палитра, акцентные цвета (градиент) и отрисовка
градиентных элементов через Pillow."""
from __future__ import annotations

import math
import os
import sys
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

from ..palette import (  # noqa: F401 — палитра общая с окном Qt
    PRESETS, DEFAULT_PRESET, Palette, DARK, LIGHT, gradient_at, MAX_SAVED, MY_PREFIX, preset_key_for,
    remember_theme, system_mode, normalize_colors, make_palette, hex_to_rgb, rgb_to_hex, mix, readable_on,
)

# Картинки (кнопки, меню, логотип) рисуются сразу в физических пикселях экрана:
# SCALE — масштаб Windows (1.0, 1.25, 1.5…), выставляется окном при запуске.
# Текст рисуется в точном размере (без последующего масштабирования, иначе он
# «мылится»), а края фигур сглаживаются суперсэмплингом (SS).
SCALE = 1.0
SS = 4


def px(v: float) -> int:
    """Логические единицы → физические пиксели (так же округляет CTkImage)."""
    return max(1, int(round(v * SCALE)))


def apply_ctk_theme(p: Palette, set_mode: bool = True) -> None:
    """Подменяет цвета встроенной темы customtkinter, чтобы все стандартные
    виджеты выглядели в едином стиле. Используются только цвета из p.roles()."""
    if set_mode:
        ctk.set_appearance_mode(p.mode)
    t = ctk.ThemeManager.theme
    two = lambda c: [c, c]  # noqa: E731
    acc, acc_hover = p.accent, p.accent_hover
    t["CTk"]["fg_color"] = two(p.bg)
    t["CTkToplevel"]["fg_color"] = two(p.bg)
    t["CTkFrame"].update(fg_color=two(p.surface), top_fg_color=two(p.surface2), border_color=two(p.border))
    t["CTkButton"].update(fg_color=two(acc), hover_color=two(acc_hover), border_color=two(p.border),
                          text_color=two(p.on_accent), text_color_disabled=two(p.muted))
    t["CTkLabel"]["text_color"] = two(p.text)
    t["CTkEntry"].update(fg_color=two(p.surface2), border_color=two(p.border), text_color=two(p.text),
                         placeholder_text_color=two(p.muted))
    t["CTkTextbox"].update(fg_color=two(p.surface2), border_color=two(p.border), text_color=two(p.text),
                           scrollbar_button_color=two(p.border), scrollbar_button_hover_color=two(p.muted))
    t["CTkSwitch"].update(fg_color=two(p.border), progress_color=two(acc), button_color=two(p.knob),
                          button_hover_color=two(p.knob_hover), text_color=two(p.text))
    t["CTkCheckBox"].update(fg_color=two(acc), hover_color=two(acc_hover), border_color=two(p.muted),
                            text_color=two(p.text), checkmark_color=two(p.on_accent))
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
    "settings": "\uE713", "list": "\uE8A5", "power": "\uE7E8", "vpn": "\uE705", "heart": "\uE006",
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


def aa_mask(size: Tuple[int, int], shape: str = "rrect", radius: float = 0) -> Image.Image:
    """Маска со сглаженными краями. Для скруглённого прямоугольника сглаживаются
    только углы (быстро даже для больших картинок)."""
    w, h = size
    if shape == "ellipse":
        big = Image.new("L", (w * SS, h * SS), 0)
        ImageDraw.Draw(big).ellipse([0, 0, w * SS - 1, h * SS - 1], fill=255)
        return big.resize((w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 255)
    r = max(0.0, min(float(radius), w / 2, h / 2))
    ri = int(math.ceil(r))
    if ri < 1:
        return mask
    corner = _aa_corner(ri, round(r, 2))
    mask.paste(corner, (0, 0))
    mask.paste(corner.transpose(Image.FLIP_LEFT_RIGHT), (w - ri, 0))
    mask.paste(corner.transpose(Image.FLIP_TOP_BOTTOM), (0, h - ri))
    mask.paste(corner.transpose(Image.ROTATE_180), (w - ri, h - ri))
    return mask


@lru_cache(maxsize=64)
def _aa_corner(ri: int, r: float) -> Image.Image:
    """Левый верхний угол ri×ri: четверть круга радиуса r, со сглаживанием."""
    big = Image.new("L", (ri * SS, ri * SS), 0)
    ImageDraw.Draw(big).ellipse([0, 0, 2 * r * SS - 1, 2 * r * SS - 1], fill=255)
    return big.resize((ri, ri), Image.LANCZOS)


def rounded(img: Image.Image, radius_px: float) -> Image.Image:
    out = img.copy()
    out.putalpha(aa_mask(img.size, "rrect", radius_px))
    return out


def pill(size: Tuple[int, int], colors: Sequence[str], radius: float, angle: str = "h") -> Image.Image:
    """Скруглённая градиентная плашка; size и radius — в логических единицах."""
    w, h = px(size[0]), px(size[1])
    g = _gradient_cached((w, h), tuple(colors), angle).copy()
    return rounded(g, radius * SCALE)


def draw_text(img: Image.Image, xy, text: str, size: int, color: str, bold: bool = False,
              anchor: str = "lm") -> None:
    ImageDraw.Draw(img).text((round(xy[0] * SCALE), round(xy[1] * SCALE)), text, font=font(px(size), bold),
                             fill=color, anchor=anchor)


def draw_icon(img: Image.Image, xy, name: str, size: int, color: str) -> bool:
    f = icon_font(px(size))
    if f is None or name not in ICONS:
        return False
    ImageDraw.Draw(img).text((round(xy[0] * SCALE), round(xy[1] * SCALE)), ICONS[name], font=f, fill=color,
                             anchor="mm")
    return True


def text_width(text: str, size: int, bold: bool = False) -> float:
    """Ширина текста в логических единицах."""
    return font(px(size), bold).getlength(text) / SCALE


def gradient_text(text: str, size: int, colors: Sequence[str], bold: bool = True) -> Image.Image:
    f = font(px(size), bold)
    bbox = f.getbbox(text)
    w, h = bbox[2] + px(2), bbox[3] + px(3)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=f, fill=255)
    g = gradient((w, h), colors, "h")
    g.putalpha(mask)
    return g


def to_ctk(img: Image.Image) -> ctk.CTkImage:
    # размер в логических единицах; CTkImage умножит его на тот же масштаб и
    # получит ровно размер картинки — Pillow тогда не пересэмплирует изображение
    size = (max(1, round(img.width / SCALE)), max(1, round(img.height / SCALE)))
    return ctk.CTkImage(light_image=img, dark_image=img, size=size)
