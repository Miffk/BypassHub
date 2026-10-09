"""Выбор цвета в стиле Discord: квадрат насыщенности/яркости и полоса оттенка."""
from __future__ import annotations

import colorsys
import tkinter as tk
from typing import TYPE_CHECKING, Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from . import theme as T
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

QUICK = ["#ff4d4d", "#ff8a3d", "#ffc53d", "#3ddc84", "#14b8a6", "#00c6ff",
         "#2b7fff", "#6366f1", "#8b5cf6", "#d946ef", "#f72585", "#94a3b8"]

SV_W, SV_H, HUE_H = 300, 190, 16


def _sv_image(hue: float, w: int, h: int, radius: float) -> Image.Image:
    """Квадрат: слева направо — насыщенность, сверху вниз — яркость."""
    r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1, 1))
    img = Image.new("RGBA", (w, h), (r, g, b, 255))
    white_mask = Image.linear_gradient("L").rotate(-90).resize((w, h))  # 255 слева → 0 справа
    img.paste((255, 255, 255, 255), (0, 0), white_mask)
    black_mask = Image.linear_gradient("L").resize((w, h))              # 0 сверху → 255 снизу
    img.paste((0, 0, 0, 255), (0, 0), black_mask)
    img.putalpha(T.aa_mask((w, h), "rrect", radius))
    return img


def _hue_image(w: int, h: int) -> Image.Image:
    strip = Image.new("RGB", (w, 1))
    strip.putdata([tuple(int(c * 255) for c in colorsys.hsv_to_rgb(x / max(1, w - 1), 1, 1)) for x in range(w)])
    img = strip.resize((w, h)).convert("RGBA")
    img.putalpha(T.aa_mask((w, h), "rrect", h / 2))
    return img


def _ring(d: int, fill: Optional[str]) -> Image.Image:
    """Маркер: белое кольцо с тенью, внутри — текущий цвет (сглаженные края)."""
    ss = T.SS
    n = d * ss
    big = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    dr = ImageDraw.Draw(big)
    dr.ellipse([0, 0, n - 1, n - 1], fill=(0, 0, 0, 70))                # мягкая тень
    pad = max(1, round(n * 0.06))
    dr.ellipse([pad, pad, n - 1 - pad, n - 1 - pad], fill=(255, 255, 255, 255))
    inner = round(n * 0.22)
    if fill:
        dr.ellipse([inner, inner, n - 1 - inner, n - 1 - inner], fill=fill)
    else:
        dr.ellipse([inner, inner, n - 1 - inner, n - 1 - inner], fill=(0, 0, 0, 0))
    return big.resize((d, d), Image.LANCZOS)


class ColorPicker(ctk.CTkToplevel):
    """Окно выбора цветов темы. Слоты (до трёх цветов градиента) — прямо здесь,
    изменения сразу применяются ко всему окну программы. «Готово» сохраняет,
    «Отмена» (или закрытие окна) возвращает прежние цвета."""

    PAUSE_MS = 110     # применить, когда указатель замер
    MAX_WAIT_MS = 500  # …но не реже, чем раз в столько, пока тянут маркер

    def __init__(self, app: "App", index: int = 0, on_close: Optional[Callable[[], None]] = None):
        super().__init__(app)
        self.app = app
        self.on_close = on_close
        appearance = app.core.settings.data["appearance"]
        self._orig_colors = list(appearance.get("colors") or [])
        self._orig_preset = appearance.get("preset")
        self.colors = T.normalize_colors(self._orig_colors)
        if index >= len(self.colors) and len(self.colors) < 3:
            self.colors.append(QUICK[(len(self.colors) * 5) % len(QUICK)])
        self.idx = min(index, len(self.colors) - 1)
        self._job = None
        self._first_change = None
        self._dirty = False

        P = W.P
        self.title("Цвета темы")
        self.resizable(False, False)
        self.configure(fg_color=P.surface)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())

        ctk.CTkLabel(self, text="Цвета темы", font=W.title_font(16), anchor="w").pack(
            fill="x", padx=20, pady=(16, 2))
        ctk.CTkLabel(self, text="Несколько цветов — градиентный фон. Изменения видны сразу.",
                     anchor="w", text_color=P.muted, font=ctk.CTkFont(size=11)).pack(fill="x", padx=20)

        self.slots = ctk.CTkFrame(self, fg_color="transparent")
        self.slots.pack(fill="x", padx=20, pady=(10, 12))
        self._slot_widgets = []

        self.sw, self.sh, self.hh = T.px(SV_W), T.px(SV_H), T.px(HUE_H)
        self.ring = T.px(20)
        pad = self.ring // 2 + 1  # место, чтобы маркер у края не обрезался
        self.pad = pad
        self.sv = tk.Canvas(self, width=self.sw + 2 * pad, height=self.sh + 2 * pad, bd=0,
                            highlightthickness=0, bg=P.surface, cursor="crosshair")
        self.sv.pack(padx=max(0, 20 - pad), anchor="w")
        self.hue = tk.Canvas(self, width=self.sw + 2 * pad, height=self.ring + 2, bd=0,
                             highlightthickness=0, bg=P.surface, cursor="hand2")
        self.hue.pack(padx=max(0, 20 - pad), pady=(10, 0), anchor="w")

        self._hue_photo = ImageTk.PhotoImage(_hue_image(self.sw, self.hh))
        self.hue.create_image(pad, (self.ring + 2) // 2, image=self._hue_photo, anchor="w")
        self._sv_item = self.sv.create_image(pad, pad, anchor="nw")
        self._sv_marker = self.sv.create_image(0, 0)
        self._hue_marker = self.hue.create_image(0, 0)
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.sv.bind(ev, self._sv_event)
            self.hue.bind(ev, self._hue_event)
        for c in (self.sv, self.hue):
            c.bind("<ButtonRelease-1>", lambda e: self._flush())

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 0))
        self.hex = ctk.CTkEntry(row, width=120, font=ctk.CTkFont(family="Consolas", size=14))
        self.hex.pack(side="left")
        self.hex.bind("<Return>", self._hex_entered)
        self.hex.bind("<FocusOut>", self._hex_entered)
        self.rgb_label = ctk.CTkLabel(row, text="", text_color=P.muted)
        self.rgb_label.pack(side="left", padx=10)

        quick = ctk.CTkFrame(self, fg_color="transparent")
        quick.pack(fill="x", padx=20, pady=(12, 0))
        self._quick_imgs = []
        step = SV_W / len(QUICK)  # быстрые цвета ровно по ширине квадрата
        for i, c in enumerate(QUICK):
            img = W.swatch([c], 20)
            self._quick_imgs.append(img)
            lbl = ctk.CTkLabel(quick, text="", image=img, width=20, height=20, cursor="hand2")
            lbl.place(x=round(i * step + (step - 20) / 2), y=0)
            lbl.bind("<Button-1>", lambda e, c=c: (self.set_color(c), self._flush()))
        quick.configure(width=SV_W, height=22)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(16, 18))
        W.GradientButton(bar, text="Готово", command=self.done, width=140).pack(side="left", padx=(0, 10))
        W.add_button(bar, "Отмена", self.cancel, secondary=True, width=140)

        self._build_slots()
        self._select(self.idx)
        self.after(10, self._center)

    # ------------------------------------------------------------------ окно
    def _center(self) -> None:
        self.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - self.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        from .app import set_dark_titlebar
        set_dark_titlebar(self, W.P.mode == "dark")
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def restyle(self) -> None:
        """Тема сменилась — подогнать фон холстов (остальное перекрашено автоматически)."""
        for c in (self.sv, self.hue):
            c.configure(bg=W.P.surface)
        self._style_slots()

    # ------------------------------------------------------------------ слоты
    def _build_slots(self) -> None:
        for child in self.slots.winfo_children():
            child.destroy()
        self._slot_widgets = []
        P = W.P
        for i, color in enumerate(self.colors):
            chip = ctk.CTkFrame(self.slots, fg_color=P.surface2, corner_radius=12, border_width=2,
                                border_color=P.surface2, cursor="hand2")
            chip.pack(side="left", padx=(0, 6))
            sw = ctk.CTkLabel(chip, text="", width=22, height=22, cursor="hand2")
            sw.pack(side="left", padx=(8, 4), pady=6)
            txt = ctk.CTkLabel(chip, text=color, font=ctk.CTkFont(family="Consolas", size=12), cursor="hand2")
            txt.pack(side="left", padx=(0, 4 if len(self.colors) > 1 else 10))
            for w in (chip, sw, txt):
                w.bind("<Button-1>", lambda e, i=i: self._select(i))
            if len(self.colors) > 1:
                ctk.CTkButton(chip, text="✕", width=22, height=22, fg_color="transparent",
                              hover_color=P.border, text_color=P.muted,
                              command=lambda i=i: self._remove(i)).pack(side="left", padx=(0, 6))
            self._slot_widgets.append((chip, sw, txt))
        if len(self.colors) < 3:
            ctk.CTkButton(self.slots, text="+", width=36, height=36, corner_radius=12, fg_color="transparent",
                          border_width=1, border_color=P.border, text_color=P.text, hover_color=P.surface2,
                          command=self._add).pack(side="left")
        self._style_slots()

    def _style_slots(self) -> None:
        P = W.P
        self._slot_imgs = []
        for i, (chip, sw, txt) in enumerate(self._slot_widgets):
            img = W.swatch([self.colors[i]], 22)
            self._slot_imgs.append(img)
            sw.configure(image=img)
            txt.configure(text=self.colors[i])
            chip.configure(border_color=P.accent if i == self.idx else P.surface2)

    def _select(self, i: int) -> None:
        self._flush()
        self.idx = i
        r, g, b = T.hex_to_rgb(self.colors[i])
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0 or not hasattr(self, "h"):
            self.h = h
        self.s, self.v = s, v
        self._render_sv()
        self._update(user=False)
        self._style_slots()

    def _add(self) -> None:
        if len(self.colors) >= 3:
            return
        self.colors.append(self.colors[self.idx])
        self._build_slots()
        self._select(len(self.colors) - 1)
        self._changed(immediate=True)

    def _remove(self, i: int) -> None:
        if len(self.colors) <= 1:
            return
        self.colors.pop(i)
        self._build_slots()
        self._select(min(self.idx if self.idx < i else self.idx - 1, len(self.colors) - 1))
        self._changed(immediate=True)

    # ------------------------------------------------------------------ цвет
    @property
    def color(self) -> str:
        r, g, b = colorsys.hsv_to_rgb(self.h, self.s, self.v)
        return T.rgb_to_hex((round(r * 255), round(g * 255), round(b * 255)))

    def set_color(self, hex_color: str) -> None:
        r, g, b = T.hex_to_rgb(hex_color)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0:  # у серых оттенков сохраняем текущий тон полосы
            self.h = h
        self.s, self.v = s, v
        self._render_sv()
        self._update()

    def _render_sv(self) -> None:
        self._sv_photo = ImageTk.PhotoImage(_sv_image(self.h, self.sw, self.sh, T.px(12)))
        self.sv.itemconfigure(self._sv_item, image=self._sv_photo)

    def _update(self, hex_from_entry: bool = False, user: bool = True) -> None:
        color = self.color
        hue_hex = T.rgb_to_hex([c * 255 for c in colorsys.hsv_to_rgb(self.h, 1, 1)])
        self._sv_ring = ImageTk.PhotoImage(_ring(self.ring, color))
        self._hue_ring = ImageTk.PhotoImage(_ring(self.ring, hue_hex))
        self.sv.itemconfigure(self._sv_marker, image=self._sv_ring)
        self.sv.coords(self._sv_marker, self.pad + self.s * self.sw, self.pad + (1 - self.v) * self.sh)
        self.hue.itemconfigure(self._hue_marker, image=self._hue_ring)
        self.hue.coords(self._hue_marker, self.pad + self.h * self.sw, (self.ring + 2) // 2)
        if not hex_from_entry:
            W.set_entry(self.hex, color)
        r, g, b = T.hex_to_rgb(color)
        self.rgb_label.configure(text=f"RGB {r}, {g}, {b}")
        if user and color != self.colors[self.idx]:
            self.colors[self.idx] = color
            chip, sw, txt = self._slot_widgets[self.idx]
            self._slot_imgs[self.idx] = W.swatch([color], 22)
            sw.configure(image=self._slot_imgs[self.idx])
            txt.configure(text=color)
            self._changed()

    def _sv_event(self, e) -> None:
        self.s = min(1.0, max(0.0, (e.x - self.pad) / self.sw))
        self.v = 1 - min(1.0, max(0.0, (e.y - self.pad) / self.sh))
        self._update()

    def _hue_event(self, e) -> None:
        self.h = min(0.9999, max(0.0, (e.x - self.pad) / self.sw))
        self._render_sv()
        self._update()

    def _hex_entered(self, _e=None) -> None:
        text = self.hex.get().strip()
        if not text.startswith("#"):
            text = "#" + text
        if len(text) == 4:  # #abc → #aabbcc
            text = "#" + "".join(ch * 2 for ch in text[1:])
        if T.normalize_colors([text]) == [text.lower()] and text.lower() != self.color:
            self.set_color(text)
            self._flush()

    # ------------------------------------------------------------------ живое применение
    def _changed(self, immediate: bool = False) -> None:
        self._dirty = True
        import time
        now = time.monotonic()
        if self._first_change is None:
            self._first_change = now
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        waited = (now - self._first_change) * 1000
        if immediate or waited >= self.MAX_WAIT_MS:
            self._flush()
        else:
            self._job = self.after(self.PAUSE_MS, self._flush)

    def _flush(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self._first_change = None
        if not self._dirty:
            return
        self._dirty = False
        a = self.app.core.settings.data["appearance"]
        a["colors"] = list(self.colors)
        a["preset"] = T.preset_key_for(a)
        self.app.apply_appearance(animate=False)

    def done(self) -> None:
        self._hex_entered()
        self._flush()
        a = self.app.core.settings.data["appearance"]
        a["preset"] = T.remember_theme(a)  # свои цвета сохраняются в «Мои темы»
        self.app.core.settings.save()
        self._close()

    def cancel(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        a = self.app.core.settings.data["appearance"]
        a["colors"], a["preset"] = list(self._orig_colors), self._orig_preset
        self.app.apply_appearance(animate=False)
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self.on_close:
            self.on_close()
