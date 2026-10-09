"""Поверхности с градиентным фоном и перекраска интерфейса на лету.

Tk не умеет полупрозрачные виджеты, поэтому фон рисуется картинкой на
tk.Canvas, а виджеты поверх него получают цвет фона в своей точке градиента:
- обычные элементы (заголовки, подписи, кнопки) — цвет градиента под ними;
- карточки — «полупрозрачную» подложку (Palette.card_on) и свои цвета
  для каждого из четырёх скруглённых углов.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, List, Optional, Sequence

import customtkinter as ctk
from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass
from PIL import Image, ImageTk

from . import theme as T


def _palette():
    from . import widgets as W  # отложенный импорт: widgets импортирует этот модуль
    return W.P


def gradient_column(stops: Sequence[str], height: int, period: int) -> List[str]:
    """Цвета по высоте: градиент «туда и обратно» с периодом period пикселей."""
    out = []
    period = max(period, 2)
    for y in range(height):
        phase = (y % (2 * period)) / period
        t = phase if phase <= 1 else 2 - phase
        out.append(T.gradient_at(stops, t))
    return out


def gradient_image(width: int, height: int, stops: Sequence[str], period: int) -> Image.Image:
    # Градиент плавный, поэтому считаем цвет для каждой 4-й строки и растягиваем
    step = 4
    col = gradient_column(stops, max(1, height // step + 1), max(1, period // step))
    strip = Image.new("RGB", (1, len(col)))
    strip.putdata([T.hex_to_rgb(c) for c in col])
    return strip.resize((max(1, width), max(1, height)), Image.BILINEAR)


def set_bg_tree(widget, color: str) -> None:
    """Задать цвет фона виджету и всем его «прозрачным» потомкам."""
    if not isinstance(widget, CTkBaseClass):
        return
    try:
        widget.configure(bg_color=color)
    except Exception:
        return
    if isinstance(widget, ctk.CTkFrame) and widget.cget("fg_color") == "transparent":
        for child in widget.winfo_children():
            set_bg_tree(child, color)


def mark_card(frame: ctk.CTkFrame) -> ctk.CTkFrame:
    """Отметить карточку: её цвет будет браться из градиента под ней."""
    frame._bh_card = True
    return frame


class _GradientMixin:
    """Общая логика: градиентная картинка + подстройка цветов прямых потомков."""

    stops_attr = "page_stops"

    def _gm_init(self) -> None:
        self._bg_item = None
        self._bg_photo = None
        self._bg_key = None
        self._job = None
        self._period = 900
        self._applied: Dict[str, tuple] = {}
        self._retries = 0

    def _stops(self) -> List[str]:
        return getattr(_palette(), self.stops_attr)

    def color_at(self, y: float) -> str:
        p = self._period
        phase = (max(0.0, y) % (2 * p)) / p
        return T.gradient_at(self._stops(), phase if phase <= 1 else 2 - phase)

    def schedule_layout(self, *_):
        if self._job is None:
            self._job = self.after(40, self.apply_layout)

    def restyle(self) -> None:
        """Палитра сменилась: перерисовать фон и пересчитать все цвета."""
        self._bg_key = None
        self._applied.clear()
        self.apply_layout()

    def _draw_background(self, width: int, height: int) -> None:
        key = (width, height, self._period, tuple(self._stops()))
        if key == self._bg_key or width < 2 or height < 2:
            return
        self._bg_key = key
        img = gradient_image(width, height, self._stops(), self._period)
        self._bg_photo = ImageTk.PhotoImage(img)
        if self._bg_item is None:
            self._bg_item = self.create_image(0, 0, image=self._bg_photo, anchor="nw")
        else:
            self.itemconfigure(self._bg_item, image=self._bg_photo)
        self.tag_lower(self._bg_item)
        self.configure(bg=self.color_at(height / 2))

    def _apply_children(self) -> None:
        P = _palette()
        pending = False
        for child in self.winfo_children():
            if not isinstance(child, CTkBaseClass):
                continue
            if not child.winfo_ismapped():
                pending = pending or child.winfo_manager() != ""
                continue
            top = child.winfo_y()
            h = child.winfo_height()
            mid = self.color_at(top + h / 2)
            if getattr(child, "_bh_card", False):
                c_top, c_bot = self.color_at(top), self.color_at(top + h)
                state = ("card", P.card_on(mid), c_top, c_bot, mid)
                if self._applied.get(str(child)) == state:
                    continue
                self._applied[str(child)] = state
                child.configure(fg_color=state[1], bg_color=mid,
                                background_corner_colors=(c_top, c_top, c_bot, c_bot))
            else:
                state = ("bg", mid)
                if self._applied.get(str(child)) == state:
                    continue
                self._applied[str(child)] = state
                set_bg_tree(child, mid)
        # элементы, которые Tk ещё не успел показать, раскрасим чуть позже
        if pending and self.winfo_ismapped() and self._retries < 20:
            self._retries += 1
            self.after(120, self.schedule_layout)
        elif not pending:
            self._retries = 0


class GradientPage(tk.Canvas, _GradientMixin):
    """Прокручиваемая страница. Сам объект — холст, в который пакуются виджеты
    страницы (как в CTkScrollableFrame); фон — градиент, прокручивается вместе
    с содержимым. Полоса прокрутки рисуется прямо на холсте."""

    THUMB_W = 6

    def __init__(self, parent, **_ignored):
        self._outer = tk.Frame(parent, bd=0, highlightthickness=0, bg=_palette().bg)
        self._view = tk.Canvas(self._outer, bd=0, highlightthickness=0, yscrollincrement=1, bg=_palette().bg)
        self._view.pack(fill="both", expand=True)
        super().__init__(self._view, bd=0, highlightthickness=0, bg=_palette().bg)
        self._gm_init()
        self._win = self._view.create_window(0, 0, window=self, anchor="nw")
        self._view.configure(yscrollcommand=self._on_scroll)
        self._thumb = None
        self._first = 0.0
        self._drag = None
        self.bind("<Configure>", self.schedule_layout, add="+")
        self._view.bind("<Configure>", self.schedule_layout, add="+")
        self.tag_bind("thumb", "<ButtonPress-1>", self._thumb_press)
        self.tag_bind("thumb", "<B1-Motion>", self._thumb_drag)

    # геометрия страницы управляется внешней рамкой
    def grid(self, **kw):
        self._outer.grid(**kw)

    def grid_forget(self):
        self._outer.grid_forget()

    def pack(self, **kw):
        self._outer.pack(**kw)

    def pack_forget(self):
        self._outer.pack_forget()

    def destroy(self):
        tk.Canvas.destroy(self)
        self._outer.destroy()

    # ----------------------------------------------------------- раскладка
    def apply_layout(self) -> None:
        self._job = None
        try:
            view_w, view_h = self._view.winfo_width(), self._view.winfo_height()
        except tk.TclError:
            return
        if view_w < 2:
            return
        self._period = max(600, int(view_h * 1.3))
        height = max(self.winfo_reqheight(), view_h)
        self._view.itemconfigure(self._win, width=view_w, height=height)
        self._view.configure(scrollregion=(0, 0, view_w, height), bg=self.color_at(0))
        self._outer.configure(bg=self.color_at(0))
        self._draw_background(view_w, height)
        self._apply_children()
        self._place_thumb()

    # ----------------------------------------------------------- прокрутка
    def scroll_units(self, delta_px: int) -> None:
        self._view.yview_scroll(int(delta_px), "units")

    def scroll_to_top(self) -> None:
        self._view.yview_moveto(0)

    def _on_scroll(self, first, last) -> None:
        self._first, self._last = float(first), float(last)
        self._place_thumb()

    def _place_thumb(self) -> None:
        first, last = self._first, getattr(self, "_last", 1.0)
        height = max(1, self.winfo_height())
        if last - first >= 0.999:
            if self._thumb is not None:
                self.delete(self._thumb)
                self._thumb = None
            return
        x = self.winfo_width() - 8
        y0, y1 = first * height + 6, last * height - 6
        color = T.mix(_palette().text, self.color_at((y0 + y1) / 2), 0.72)
        if self._thumb is None:
            self._thumb = self.create_line(x, y0, x, y1, width=self.THUMB_W, capstyle="round",
                                           fill=color, tags=("thumb",))
        else:
            self.coords(self._thumb, x, y0, x, y1)
            self.itemconfigure(self._thumb, fill=color)
        self.tag_raise(self._thumb)

    def _thumb_press(self, event) -> None:
        self._drag = (event.y_root, self._first)

    def _thumb_drag(self, event) -> None:
        if not self._drag:
            return
        y0, first = self._drag
        height = max(1, self.winfo_height())
        self._view.yview_moveto(first + (event.y_root - y0) / height)


class GradientPanel(tk.Canvas, _GradientMixin):
    """Неподвижная панель с градиентом (боковое меню, журнал)."""

    def __init__(self, parent, stops_attr: str = "side_stops", **kw):
        super().__init__(parent, bd=0, highlightthickness=0, bg=_palette().bg, **kw)
        self.stops_attr = stops_attr
        self._gm_init()
        self.bind("<Configure>", self.schedule_layout, add="+")

    def apply_layout(self) -> None:
        self._job = None
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        self._period = max(600, h)
        self._draw_background(w, h)
        self._apply_children()


# ===================================================================== перекраска

COLOR_ATTRS = (
    "fg_color", "bg_color", "text_color", "text_color_disabled", "border_color", "hover_color",
    "button_color", "button_hover_color", "progress_color", "selected_color", "selected_hover_color",
    "unselected_color", "unselected_hover_color", "placeholder_text_color", "scrollbar_button_color",
    "scrollbar_button_hover_color", "dropdown_fg_color", "dropdown_hover_color", "dropdown_text_color",
    "checkmark_color",
)


def _remap(value, mapping: Dict[str, str]):
    if isinstance(value, str):
        return mapping.get(value.lower())
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value):
        new = [mapping.get(v.lower(), v) for v in value]
        if new != list(value):
            return type(value)(new) if isinstance(value, tuple) else new
    return None


def build_mapping(old: T.Palette, new: T.Palette) -> Dict[str, str]:
    o, n = old.roles(), new.roles()
    return {o[k].lower(): n[k] for k in o if o[k] and n.get(k) and o[k].lower() != n[k].lower()}


def recolor_tree(root, mapping: Dict[str, str]) -> None:
    """Обойти виджеты сверху вниз и заменить цвета старой палитры на новые;
    у кого есть restyle() (картинки, градиентные поверхности) — перерисовать."""
    stack = [root]
    gradient_surfaces = []
    while stack:
        w = stack.pop()
        if isinstance(w, (CTkBaseClass, ctk.CTk, ctk.CTkToplevel)) and mapping:
            changes = {}
            for attr in COLOR_ATTRS:
                try:
                    value = w.cget(attr)
                except Exception:
                    continue
                new = _remap(value, mapping)
                if new is not None:
                    changes[attr] = new
            if changes:
                try:
                    w.configure(**changes)
                except Exception:
                    pass
        if isinstance(w, _GradientMixin):
            gradient_surfaces.append(w)
        elif hasattr(w, "restyle"):
            try:
                w.restyle()
            except Exception:
                pass
        try:
            children = w.winfo_children()
        except tk.TclError:
            continue
        stack.extend(reversed(children))
    # градиент — в конце, чтобы цвета прямых потомков пересчитались после перекраски
    for s in gradient_surfaces:
        s.restyle()


def crossfade(window, apply: Callable[[], None], duration_ms: int = 260) -> None:
    """Плавная смена оформления: поверх окна кладётся снимок старого вида,
    под ним выполняется apply(), затем снимок растворяется."""
    overlay: Optional[tk.Toplevel] = None
    try:
        if window.winfo_viewable():
            overlay = _snapshot(window)
    except Exception:
        overlay = None
    try:
        apply()
    finally:
        if overlay is not None:
            window.update_idletasks()
            _fade(window, overlay, duration_ms)


def _snapshot(window) -> Optional[tk.Toplevel]:
    from PIL import ImageGrab
    x, y = window.winfo_rootx(), window.winfo_rooty()
    w, h = window.winfo_width(), window.winfo_height()
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    if x < 0 or y < 0 or x + w > sw or y + h > sh or w < 10 or h < 10:
        return None  # окно частично за пределами основного экрана — без анимации
    import os
    import sys
    kw = {"xdisplay": os.environ["DISPLAY"]} if sys.platform != "win32" and os.environ.get("DISPLAY") else {}
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), **kw)
    top = tk.Toplevel(window)
    top.overrideredirect(True)
    top.geometry(f"{w}x{h}+{x}+{y}")
    photo = ImageTk.PhotoImage(img)
    label = tk.Label(top, image=photo, bd=0, highlightthickness=0)
    label.image = photo
    label.pack()
    top.lift(window)
    top.update()
    window.after(2000, lambda: top.winfo_exists() and top.destroy())  # страховка
    return top


def _fade(window, overlay: tk.Toplevel, duration_ms: int) -> None:
    steps = 10
    delay = max(10, duration_ms // steps)

    def step(i: int) -> None:
        try:
            if not overlay.winfo_exists():
                return
            if i >= steps:
                overlay.destroy()
                return
            overlay.attributes("-alpha", 1 - (i + 1) / steps)
            window.after(delay, lambda: step(i + 1))
        except tk.TclError:
            pass
    step(0)
