"""Общие элементы интерфейса в едином минималистичном стиле.

Цвета берутся из текущей палитры (theme.Palette). При смене оформления окно
не пересоздаётся: surface.recolor_tree() заменяет цвета старой палитры на новые,
а виджеты-картинки перерисовываются через restyle().
"""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw

from . import theme as T
from .surface import GradientPage, mark_card

GREEN = ("#16a34a", "#3ddc84")
RED = ("#dc2626", "#ff6b6b")
YELLOW = ("#c27c0e", "#f6c343")

P: T.Palette = T.make_palette({"mode": "dark"})
GRAY = P.muted
CARD = P.surface
TEXT = P.text


# Отступ справа у элементов страницы — место под полосу прокрутки
PAGE_PADX = (4, 18)


def apply_theme(palette: T.Palette, set_mode: bool = True) -> None:
    global P, GRAY, CARD, TEXT
    P = palette
    GRAY, CARD, TEXT = palette.muted, palette.surface, palette.text
    T.apply_ctk_theme(palette, set_mode=set_mode)


def title_font(size: int = 20) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold")


def page_title(parent, text: str, subtitle: str = "") -> None:
    ctk.CTkLabel(parent, text=text, font=title_font(26), anchor="w").pack(fill="x", padx=(6, 18), pady=(6, 0))
    if subtitle:
        ctk.CTkLabel(parent, text=subtitle, anchor="w", text_color=GRAY).pack(fill="x", padx=(6, 18))
    ctk.CTkFrame(parent, height=12, fg_color="transparent").pack()


def section(parent, title: str, hint: Optional[str] = None) -> ctk.CTkFrame:
    """Карточка с заголовком; возвращает внутренний фрейм для содержимого."""
    card = mark_card(ctk.CTkFrame(parent, fg_color=P.surface, corner_radius=14))
    card.pack(fill="x", padx=PAGE_PADX, pady=(0, 12))
    ctk.CTkLabel(card, text=title, font=title_font(15), anchor="w").pack(fill="x", padx=18, pady=(14, 0))
    if hint:
        ctk.CTkLabel(card, text=hint, anchor="w", justify="left", text_color=GRAY,
                     wraplength=640).pack(fill="x", padx=18, pady=(2, 0))
    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=18, pady=(8, 16))
    return body


def row(parent, label: str, widget_factory, label_width: int = 230, hint: Optional[str] = None):
    fr = ctk.CTkFrame(parent, fg_color="transparent")
    fr.pack(fill="x", pady=4)
    ctk.CTkLabel(fr, text=label, width=label_width, anchor="w").pack(side="left")
    w = widget_factory(fr)
    w.pack(side="left", fill="x", expand=True)
    if hint:
        ctk.CTkLabel(parent, text=hint, anchor="w", justify="left", text_color=GRAY,
                     wraplength=640, font=ctk.CTkFont(size=11)).pack(fill="x", pady=(0, 4))
    return w


def button_bar(parent) -> ctk.CTkFrame:
    fr = ctk.CTkFrame(parent, fg_color="transparent")
    fr.pack(fill="x", pady=(8, 0), padx=PAGE_PADX if isinstance(parent, GradientPage) else 0)
    return fr


def add_button(bar, text: str, command, secondary: bool = False, width: int = 0):
    if secondary:
        b = ctk.CTkButton(bar, text=text, command=command, width=width or 140, height=36, corner_radius=10,
                          fg_color="transparent", border_width=1, border_color=P.border,
                          text_color=P.text, hover_color=P.surface2)
    else:
        b = GradientButton(bar, text=text, command=command, width=width or 0)
    b.pack(side="left", padx=(0, 8), pady=2)
    return b


def set_entry(entry: ctk.CTkEntry, value) -> None:
    entry.delete(0, "end")
    entry.insert(0, "" if value is None else str(value))


def set_text(box: ctk.CTkTextbox, value: str, readonly: bool = False) -> None:
    box.configure(state="normal")
    box.delete("1.0", "end")
    box.insert("end", value)
    if readonly:
        box.configure(state="disabled")


def set_switch(sw, on: bool) -> None:
    if bool(sw.get()) != bool(on):
        sw.select() if on else sw.deselect()


# ====================================================================== градиентные виджеты

class _ImageWidget(ctk.CTkLabel):
    def __init__(self, parent, width: int, height: int, **kw):
        super().__init__(parent, text="", width=width, height=height, fg_color="transparent", **kw)
        self._iw, self._ih = width, height

    def _show(self, img: Image.Image) -> None:
        self._img = T.to_ctk(img)  # ссылка, чтобы картинку не собрал GC
        super().configure(image=self._img)

    def restyle(self) -> None:
        self._render()


class GradientButton(_ImageWidget):
    """Основная кнопка: градиент из акцентных цветов."""

    def __init__(self, parent, text: str, command: Optional[Callable] = None, width: int = 0, height: int = 36):
        self._gtext = text
        self._gcommand = command
        self._gstate = "normal"
        self._ghover = False
        width = max(width or 0, self._auto_width(text))
        super().__init__(parent, width, height, cursor="hand2")
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._click)
        self._render()

    @staticmethod
    def _auto_width(text: str) -> int:
        return int(T.text_width(text, 13, True)) + 36

    def _set_hover(self, on: bool) -> None:
        self._ghover = on
        self._render()

    def _click(self, _e=None) -> None:
        if self._gstate != "disabled" and self._gcommand:
            self._gcommand()

    def _render(self) -> None:
        w, h = self._iw, self._ih
        if self._gstate == "disabled":
            img = T.pill((w, h), [P.surface2], 10)
            color = P.muted
        else:
            img = T.pill((w, h), P.accents, 10)
            if self._ghover:
                overlay = Image.new("RGBA", img.size, (255, 255, 255, 38))
                img = Image.composite(Image.alpha_composite(img, overlay), img, img.getchannel("A"))
            color = T.readable_on(P.accents)
        T.draw_text(img, (w / 2, h / 2), self._gtext, 13, color, bold=True, anchor="mm")
        self._show(img)

    def configure(self, require_redraw=False, **kwargs):  # noqa: D401
        changed = False
        if "text" in kwargs:
            self._gtext = kwargs.pop("text")
            changed = True
        if "state" in kwargs:
            self._gstate = kwargs.pop("state")
            changed = True
        if "command" in kwargs:
            self._gcommand = kwargs.pop("command")
        if kwargs:
            super().configure(require_redraw=require_redraw, **kwargs)
        if changed:
            self._render()

    def cget(self, key):
        if key == "text":
            return self._gtext
        if key == "state":
            return self._gstate
        return super().cget(key)


class GradientSwitch(_ImageWidget):
    """Большой переключатель с градиентной дорожкой и плавным ходом ползунка."""

    def __init__(self, parent, command: Optional[Callable[[], None]] = None, width: int = 66, height: int = 36):
        self._on = False
        self._pos = 0.0
        self._gstate = "normal"
        self._gcommand = command
        super().__init__(parent, width, height, cursor="hand2")
        self.bind("<Button-1>", self._click)
        self._render()

    def get(self) -> int:
        return 1 if self._on else 0

    def select(self) -> None:
        self._set(True)

    def deselect(self) -> None:
        self._set(False)

    def _set(self, on: bool, animate: bool = True) -> None:
        self._on = on
        self._animate() if animate else self._render()

    def _click(self, _e=None) -> None:
        if self._gstate == "disabled":
            return
        self._set(not self._on)
        if self._gcommand:
            self._gcommand()

    def _animate(self) -> None:
        target = 1.0 if self._on else 0.0
        step = 0.25 if target > self._pos else -0.25
        self._pos = target if abs(target - self._pos) <= 0.25 else self._pos + step
        self._render()
        if self._pos != target:
            self.after(16, self._animate)

    def _render(self) -> None:
        w, h = self._iw, self._ih
        off = T.pill((w, h), [P.border], h // 2)
        on = T.pill((w, h), P.accents, h // 2)
        img = Image.blend(off, on, self._pos) if 0 < self._pos < 1 else (on if self._pos >= 1 else off)
        img = img.copy()
        if self._gstate == "disabled":
            img.putalpha(img.getchannel("A").point(lambda a: a * 0.5))
        pad = T.px(4)
        knob = img.height - 2 * pad
        x = pad + round((img.width - 2 * pad - knob) * self._pos)
        img.paste((255, 255, 255, 255), (x, pad), T.aa_mask((knob, knob), "ellipse"))
        self._show(img)

    def configure(self, require_redraw=False, **kwargs):
        if "state" in kwargs:
            self._gstate = kwargs.pop("state")
            self._render()
        if "command" in kwargs:
            self._gcommand = kwargs.pop("command")
        if kwargs:
            super().configure(require_redraw=require_redraw, **kwargs)


class NavItem(_ImageWidget):
    def __init__(self, parent, text: str, icon: str, command: Callable[[], None], width: int = 184, height: int = 42):
        self._gtext, self._icon = text, icon
        self._active = False
        self._ghover = False
        super().__init__(parent, width, height, cursor="hand2")
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False))
        self.bind("<Button-1>", lambda e: command())
        self._render()

    def set_active(self, active: bool) -> None:
        self._set(active=active)

    def _set(self, active: Optional[bool] = None, hover: Optional[bool] = None) -> None:
        if active is not None:
            self._active = active
        if hover is not None:
            self._ghover = hover
        self._render()

    def _render(self) -> None:
        w, h = self._iw, self._ih
        if self._active:
            img = T.pill((w, h), P.accents, 12)
            color = T.readable_on(P.accents)
        elif self._ghover:
            img = T.pill((w, h), [P.surface2], 12)
            color = P.text
        else:
            img = Image.new("RGBA", (T.px(w), T.px(h)), (0, 0, 0, 0))
            color = P.muted
        x = 16
        if T.draw_icon(img, (x + 8, h / 2), self._icon, 15, color):
            x += 28
        T.draw_text(img, (x, h / 2), self._gtext, 13, color, bold=self._active)
        self._show(img)


class GradientText(_ImageWidget):
    """Надпись, залитая градиентом акцентных цветов (логотип)."""

    def __init__(self, parent, text: str, size: int):
        self._gtext, self._gsize = text, size
        img = T.gradient_text(text, size, P.accents)
        super().__init__(parent, round(img.width / T.SCALE), round(img.height / T.SCALE))
        self._render()

    def _render(self) -> None:
        self._show(T.gradient_text(self._gtext, self._gsize, P.accents))


class Hero(ctk.CTkLabel):
    """Градиентная плашка-заголовок на главной; растягивается по ширине."""

    def __init__(self, parent, height: int = 132):
        super().__init__(parent, text="", height=height, fg_color="transparent")
        self._hh = height
        self._title, self._subtitle = "", ""
        self._width = 0
        self.bind("<Configure>", self._on_resize)

    def restyle(self) -> None:
        self._render()

    def set_text(self, title: str, subtitle: str) -> None:
        if (title, subtitle) != (self._title, self._subtitle):
            self._title, self._subtitle = title, subtitle
            self._render()

    def _on_resize(self, event) -> None:
        width = int(event.width / max(self._get_widget_scaling(), 0.01))
        if abs(width - self._width) > 4:
            self._width = width
            self._render()

    def _render(self) -> None:
        w, h = max(self._width, 200), self._hh
        s = T.SCALE
        img = T.gradient((T.px(w), T.px(h)), P.accents, "diag")
        # мягкие декоративные круги — отдельным слоем, чтобы они просвечивали
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.ellipse([w * s - 220 * s, -90 * s, w * s + 40 * s, 170 * s], fill=(255, 255, 255, 30))
        d.ellipse([w * s - 120 * s, 40 * s, w * s + 60 * s, 220 * s], fill=(255, 255, 255, 22))
        img = T.rounded(Image.alpha_composite(img, layer), 18 * s)
        color = T.readable_on(P.accents)
        T.draw_text(img, (26, 48), self._title, 24, color, bold=True)
        T.draw_text(img, (26, 86), self._subtitle, 13, color)
        self._img = T.to_ctk(img)
        self.configure(image=self._img)


def swatch(colors, size: int = 34, selected: bool = False) -> ctk.CTkImage:
    n = T.px(size)
    img = T.gradient((n, n), colors, "diag")
    img.putalpha(T.aa_mask((n, n), "ellipse"))
    if selected:  # белое кольцо внутри кружка, со сглаженными краями
        ss = T.SS
        ring = Image.new("L", (n * ss, n * ss), 0)
        inset, width = T.px(3) * ss, max(ss, round(2 * T.SCALE * ss))
        ImageDraw.Draw(ring).ellipse([inset, inset, n * ss - inset, n * ss - inset], outline=255, width=width)
        img.paste((255, 255, 255, 255), (0, 0), ring.resize((n, n), Image.LANCZOS))
    return T.to_ctk(img)
