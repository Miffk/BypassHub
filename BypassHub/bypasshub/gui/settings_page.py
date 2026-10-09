from __future__ import annotations

from tkinter import colorchooser
from typing import TYPE_CHECKING, List

import customtkinter as ctk

from .. import APP_NAME, __version__, winutil
from . import glass
from . import theme as T
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

MODES = {"dark": "Тёмная", "light": "Светлая", "system": "Как в Windows"}


class SettingsPage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, fg_color=W.P.window_bg, scrollbar_button_color=W.P.border)
        self.app = app
        s = app.core.settings
        self._glass_job = None
        W.page_title(self, "Настройки", "Оформление, запуск и поведение программы")

        # ------------------------------------------------------------ оформление
        a = s.data["appearance"]
        body = W.section(self, "Оформление",
                         "Выберите тему и цвета. Если выбрать несколько цветов, элементы интерфейса "
                         "будут залиты плавным градиентом.")
        self.mode = W.row(body, "Тема", lambda p: ctk.CTkSegmentedButton(
            p, values=list(MODES.values()), command=self._mode_changed))
        self.mode.set(MODES.get(a.get("mode"), MODES["dark"]))

        ctk.CTkLabel(body, text="Готовые наборы", anchor="w").pack(fill="x", pady=(10, 4))
        presets = ctk.CTkFrame(body, fg_color="transparent")
        presets.pack(fill="x")
        self._swatch_imgs = []
        for i, (name, colors) in enumerate(T.PRESETS.items()):
            cell = ctk.CTkFrame(presets, fg_color="transparent")
            cell.grid(row=0, column=i, padx=(0, 10))
            img = W.swatch(colors, 38, selected=(a.get("preset") == name))
            self._swatch_imgs.append(img)
            ctk.CTkButton(cell, text="", image=img, width=44, height=44, fg_color="transparent",
                          hover_color=W.P.surface2, corner_radius=22,
                          command=lambda n=name: self._preset(n)).pack()
            ctk.CTkLabel(cell, text=name, text_color=W.P.muted, font=ctk.CTkFont(size=11)).pack()

        ctk.CTkLabel(body, text="Свои цвета (до трёх — градиент)", anchor="w").pack(fill="x", pady=(12, 4))
        self.custom = ctk.CTkFrame(body, fg_color="transparent")
        self.custom.pack(fill="x")
        self._render_custom()

        # ------------------------------------------------------------ прозрачность
        body = W.section(self, "Прозрачность и размытие",
                         "Фон окна становится полупрозрачным — видно рабочий стол и открытые окна. Карточки "
                         "остаются непрозрачными, чтобы текст хорошо читался. Работает в Windows 10/11.")
        self.glass_sw = ctk.CTkSwitch(body, text="Прозрачный фон окна", command=self._glass_changed)
        self.glass_sw.pack(anchor="w", pady=3)
        W.set_switch(self.glass_sw, a.get("glass"))

        self.opacity_label = ctk.CTkLabel(body, text="", anchor="w")
        self.opacity_label.pack(fill="x", pady=(8, 0))
        self.opacity = ctk.CTkSlider(body, from_=0, to=100, number_of_steps=100,
                                     command=lambda v: self._slider("opacity", v))
        self.opacity.pack(fill="x")
        self.opacity.set(int(a.get("opacity", 80)))

        self.blur_sw = ctk.CTkSwitch(body, text="Размытие того, что под окном", command=self._glass_changed)
        self.blur_sw.pack(anchor="w", pady=(12, 3))
        W.set_switch(self.blur_sw, a.get("blur", True))
        self.blur_label = ctk.CTkLabel(body, text="", anchor="w")
        self.blur_label.pack(fill="x")
        self.blur = ctk.CTkSlider(body, from_=1, to=100, number_of_steps=99,
                                  command=lambda v: self._slider("blur_level", v))
        self.blur.pack(fill="x")
        self.blur.set(int(a.get("blur_level", 70)))
        self.glass_info = ctk.CTkLabel(body, text="", anchor="w", text_color=W.P.muted,
                                       font=ctk.CTkFont(size=11), justify="left", wraplength=640)
        self.glass_info.pack(fill="x", pady=(6, 0))
        self._update_glass_labels()

        # ------------------------------------------------------------ запуск
        body = W.section(self, "Запуск")
        self.autostart = ctk.CTkSwitch(body, text="Запускать BypassHub при включении компьютера",
                                       command=self._autostart_changed)
        self.autostart.pack(anchor="w", pady=3)
        ctk.CTkLabel(body, text="Через Планировщик заданий Windows — с правами администратора и без запроса UAC.",
                     anchor="w", text_color=W.P.muted, font=ctk.CTkFont(size=11)).pack(fill="x")
        self._switch(body, "Запускать свёрнутым в трей", "start_minimized")
        self._switch(body, "При запуске включать то, что было включено (zapret, прокси)", "restore_state")

        body = W.section(self, "Окно и выход")
        self._switch(body, "Кнопка «закрыть» сворачивает в трей", "close_to_tray")
        self._switch(body, "При выходе выключать zapret (режим «Программа») и прокси", "stop_on_exit")

        body = W.section(self, "Данные")
        ctk.CTkLabel(body, text=f"Папка: {app.core.paths.root}", anchor="w").pack(fill="x")
        ctk.CTkLabel(body, text="Старые версии zapret и tg-ws-proxy удаляются сразу после обновления, "
                                "временные файлы загрузки — при каждом запуске.", anchor="w", justify="left",
                     wraplength=640, text_color=W.P.muted, font=ctk.CTkFont(size=11)).pack(fill="x")
        bar = W.button_bar(body)
        W.add_button(bar, "Открыть папку", lambda: winutil.open_path(app.core.paths.root), secondary=True)

        body = W.section(self, "О программе")
        ctk.CTkLabel(body, text=f"{APP_NAME} {__version__} — управление zapret-discord-youtube и tg-ws-proxy "
                                "в одном окне.", anchor="w", justify="left", wraplength=640).pack(fill="x")
        ctk.CTkLabel(body, text="Сами проекты принадлежат их авторам (Flowseal, bol-van). BypassHub скачивает "
                                "только официальные релизы с GitHub.", anchor="w", justify="left",
                     wraplength=640, text_color=W.P.muted).pack(fill="x", pady=(4, 0))
        bar = W.button_bar(body)
        W.add_button(bar, "zapret-discord-youtube", lambda: winutil.open_url(
            "https://github.com/Flowseal/zapret-discord-youtube"), secondary=True, width=200)
        W.add_button(bar, "tg-ws-proxy", lambda: winutil.open_url(
            "https://github.com/Flowseal/tg-ws-proxy"), secondary=True)

        self.on_show()

    # ------------------------------------------------------------ оформление
    @property
    def _appearance(self) -> dict:
        return self.app.core.settings.data["appearance"]

    def _save_and_rebuild(self) -> None:
        self.app.core.settings.save()
        # пересоздание чуть позже, чтобы текущий обработчик клика успел завершиться
        self.app.after(10, self.app.rebuild)

    def _mode_changed(self, label: str) -> None:
        self._appearance["mode"] = next((k for k, v in MODES.items() if v == label), "dark")
        self._save_and_rebuild()

    def _preset(self, name: str) -> None:
        self._appearance["preset"] = name
        self._appearance["colors"] = list(T.PRESETS[name])
        self._save_and_rebuild()

    def _render_custom(self) -> None:
        for child in self.custom.winfo_children():
            child.destroy()
        colors: List[str] = list(self._appearance.get("colors") or [])
        self._custom_imgs = []
        for i, color in enumerate(colors):
            img = W.swatch([color], 30)
            self._custom_imgs.append(img)
            cell = ctk.CTkFrame(self.custom, fg_color=W.P.surface2, corner_radius=12)
            cell.pack(side="left", padx=(0, 8))
            ctk.CTkButton(cell, text=color, image=img, compound="left", width=120, height=40,
                          fg_color="transparent", hover_color=W.P.border, text_color=W.P.text,
                          command=lambda i=i: self._pick(i)).pack(side="left", padx=(4, 0), pady=2)
            if len(colors) > 1:
                ctk.CTkButton(cell, text="✕", width=28, height=28, fg_color="transparent",
                              hover_color=W.P.border, text_color=W.P.muted,
                              command=lambda i=i: self._remove_color(i)).pack(side="left", padx=(0, 4))
        if len(colors) < 3:
            ctk.CTkButton(self.custom, text="+ Цвет", width=90, height=40, corner_radius=12,
                          fg_color="transparent", border_width=1, border_color=W.P.border,
                          text_color=W.P.text, hover_color=W.P.surface2,
                          command=lambda: self._pick(len(colors))).pack(side="left")

    def _pick(self, index: int) -> None:
        colors = list(self._appearance.get("colors") or [])
        initial = colors[index] if index < len(colors) else W.P.accent
        _, hex_color = colorchooser.askcolor(color=initial, parent=self.app, title="Выберите цвет")
        if not hex_color:
            return
        if index < len(colors):
            colors[index] = hex_color.lower()
        else:
            colors.append(hex_color.lower())
        self._appearance["colors"] = T.normalize_colors(colors)
        self._appearance["preset"] = "custom"
        self._save_and_rebuild()

    def _remove_color(self, index: int) -> None:
        colors = list(self._appearance.get("colors") or [])
        if len(colors) > 1:
            colors.pop(index)
            self._appearance["colors"] = colors
            self._appearance["preset"] = "custom"
            self._save_and_rebuild()

    # ------------------------------------------------------------ стекло
    def _glass_changed(self) -> None:
        glass_was = bool(self._appearance.get("glass"))
        self._appearance["glass"] = bool(self.glass_sw.get())
        self._appearance["blur"] = bool(self.blur_sw.get())
        self.app.core.settings.save()
        if glass_was != self._appearance["glass"]:
            # фон страниц зависит от режима стекла — интерфейс нужно пересоздать
            self._save_and_rebuild()
        else:
            self._apply_glass()

    def _slider(self, key: str, value: float) -> None:
        self._appearance[key] = int(value)
        self._update_glass_labels()
        if self._glass_job:
            self.after_cancel(self._glass_job)
        self._glass_job = self.after(120, self._apply_glass)

    def _apply_glass(self) -> None:
        self._glass_job = None
        self.app.core.settings.save()
        desc = self.app.apply_glass()
        self._update_glass_labels(desc)

    def _update_glass_labels(self, desc: str = "") -> None:
        a = self._appearance
        self.opacity_label.configure(text=f"Непрозрачность фона: {int(a.get('opacity', 80))}%")
        level = int(a.get("blur_level", 70))
        self.blur_label.configure(text=f"Сила размытия: {level}% — {glass.blur_level_name(level)}")
        enabled = bool(a.get("glass"))
        for w in (self.opacity, self.blur, self.blur_sw):
            w.configure(state="normal" if enabled else "disabled")
        info = ("Windows поддерживает два вида размытия: мягкое (Aero) и сильное (Acrylic) — ползунок "
                "переключает их на отметке 50%.")
        if desc and enabled:
            info += f" Сейчас: {desc}."
        self.glass_info.configure(text=info)

    # ------------------------------------------------------------ запуск
    def _switch(self, parent, text: str, key: str) -> ctk.CTkSwitch:
        s = self.app.core.settings
        sw = ctk.CTkSwitch(parent, text=text)
        sw.configure(command=lambda: s.set("app", key, bool(sw.get())))
        W.set_switch(sw, s.get("app", key))
        sw.pack(anchor="w", pady=3)
        return sw

    def on_show(self) -> None:
        def done(enabled, err):
            if not err:
                W.set_switch(self.autostart, enabled)
        self.app.run_task(winutil.autostart_enabled, done)

    def _autostart_changed(self) -> None:
        on = bool(self.autostart.get())

        def work():
            winutil.set_autostart(on)
            self.app.core.settings.set("app", "autostart", on)

        def done(_, err):
            if err:
                W.set_switch(self.autostart, not on)
                self.app.error(f"Не удалось изменить автозапуск: {err}")
        self.app.run_task(work, done)
