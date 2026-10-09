from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, List

import customtkinter as ctk

from .. import APP_NAME, __version__, backup, hotkeys, winutil
from . import theme as T
from . import widgets as W
from .surface import GradientPage

if TYPE_CHECKING:
    from .app import App

MODES = {"dark": "Тёмная", "light": "Светлая", "system": "Как в Windows"}


class SettingsPage(GradientPage):
    def __init__(self, parent, app: "App"):
        super().__init__(parent)
        self.app = app
        s = app.core.settings
        self._tint_job = None
        W.page_title(self, "Настройки", "Оформление, запуск и поведение программы")

        # ------------------------------------------------------------ оформление
        a = s.data["appearance"]
        body = W.section(self, "Оформление",
                         "Выберите тему и цвета — в них окрасится всё окно. Если выбрать несколько цветов, "
                         "фон станет плавным градиентом.")
        self.mode = W.row(body, "Тема", lambda p: ctk.CTkSegmentedButton(
            p, values=list(MODES.values()), command=self._mode_changed))
        self.mode.set(MODES.get(a.get("mode"), MODES["dark"]))

        ctk.CTkLabel(body, text="Готовые наборы", anchor="w").pack(fill="x", pady=(10, 4))
        presets = ctk.CTkFrame(body, fg_color="transparent")
        presets.pack(fill="x")
        self._swatch_btns = {}
        for i, name in enumerate(T.PRESETS):
            cell = ctk.CTkFrame(presets, fg_color="transparent")
            cell.grid(row=0, column=i, padx=(0, 10))
            btn = ctk.CTkButton(cell, text="", width=44, height=44, fg_color="transparent",
                                hover_color=W.P.surface2, corner_radius=22,
                                command=lambda n=name: self._preset(n))
            btn.pack()
            self._swatch_btns[name] = btn
            ctk.CTkLabel(cell, text=name, text_color=W.P.muted, font=ctk.CTkFont(size=11)).pack()

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x", pady=(14, 4))
        ctk.CTkLabel(head, text="Мои темы", anchor="w").pack(side="left")
        ctk.CTkButton(head, text="Сохранить текущие цвета", width=190, height=26, corner_radius=8,
                      fg_color="transparent", border_width=1, border_color=W.P.border, text_color=W.P.text,
                      hover_color=W.P.surface2, command=self._save_current).pack(side="left", padx=12)
        self.mythemes = ctk.CTkFrame(body, fg_color="transparent")
        self.mythemes.pack(fill="x")

        ctk.CTkLabel(body, text="Свои цвета (до трёх — градиент)", anchor="w").pack(fill="x", pady=(12, 4))
        self.custom = ctk.CTkFrame(body, fg_color="transparent")
        self.custom.pack(fill="x")

        self.tint_label = ctk.CTkLabel(body, text="", anchor="w")
        self.tint_label.pack(fill="x", pady=(14, 0))
        self.tint = ctk.CTkSlider(body, from_=0, to=100, number_of_steps=100, command=self._tint_changed)
        self.tint.pack(fill="x")
        self.tint.set(int(a.get("tint", 70)))
        self._refresh_appearance_controls()

        # ------------------------------------------------------------ запуск
        body = W.section(self, "Запуск")
        self.autostart = ctk.CTkSwitch(body, text="Запускать BypassHub при включении компьютера",
                                       command=self._autostart_changed)
        self.autostart.pack(anchor="w", pady=3)
        ctk.CTkLabel(body, text="Через Планировщик заданий Windows — с правами администратора и без запроса UAC.",
                     anchor="w", text_color=W.P.muted, font=ctk.CTkFont(size=11)).pack(fill="x")
        self._switch(body, "Запускать свёрнутым в трей", "start_minimized")
        self._switch(body, "При запуске включать то, что было включено (zapret, прокси)", "restore_state")

        # ------------------------------------------------------------ горячие клавиши
        body = W.section(self, "Горячие клавиши",
                         "Работают в любой программе, даже когда BypassHub свёрнут в трей. Нажмите на "
                         "сочетание, чтобы изменить его, затем нажмите новые клавиши (Esc — отмена). "
                         "Нужен хотя бы один из модификаторов Ctrl или Alt.")
        self.hk_switch = ctk.CTkSwitch(body, text="Включить горячие клавиши", command=self._hk_toggle)
        self.hk_switch.pack(anchor="w", pady=(0, 6))
        W.set_switch(self.hk_switch, s.data["hotkeys"].get("enabled"))
        self.hk_buttons = {}
        for action, label in hotkeys.ACTIONS.items():
            fr = ctk.CTkFrame(body, fg_color="transparent")
            fr.pack(fill="x", pady=3)
            ctk.CTkLabel(fr, text=label, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(fr, text="✕", width=32, height=32, fg_color="transparent", hover_color=W.P.surface2,
                          text_color=W.P.muted, command=lambda a=action: self._hk_set(a, "")).pack(side="right")
            btn = ctk.CTkButton(fr, text="", width=190, height=32, corner_radius=8, fg_color=W.P.surface2,
                                hover_color=W.P.border, text_color=W.P.text, border_width=1,
                                border_color=W.P.border, command=lambda a=action: self._hk_capture(a))
            btn.pack(side="right", padx=(0, 4))
            self.hk_buttons[action] = btn
        self.hk_info = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=640,
                                    font=ctk.CTkFont(size=11))
        self.hk_info.pack(fill="x", pady=(4, 0))
        bar = W.button_bar(body)
        W.add_button(bar, "По умолчанию", self._hk_defaults, secondary=True)
        self._hk_capturing = None
        self._hk_render(app.hotkeys.failed if hasattr(app, "hotkeys") else [])

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

        body = W.section(self, "Резервная копия настроек",
                         "Все настройки в одном файле: BypassHub, zapret (Game Filter, IPSet, фейки, списки, "
                         "исключения) и TG WS Proxy. Удобно для переноса на другой компьютер. В файле есть "
                         "secret прокси — не публикуйте его.")
        bar = W.button_bar(body)
        W.add_button(bar, "Экспорт", self._export)
        W.add_button(bar, "Импорт", self._import, secondary=True)

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

    def _save_and_apply(self) -> None:
        self.app.core.settings.save()
        # применяем чуть позже, чтобы текущий обработчик клика успел завершиться
        self.app.after(10, self._apply_now)

    def _apply_now(self) -> None:
        self.app.apply_appearance()
        self._refresh_appearance_controls()

    def _refresh_appearance_controls(self) -> None:
        """Обновить элементы «Оформления» — только то, что изменилось."""
        a = self._appearance
        self.mode.set(MODES.get(a.get("mode"), MODES["dark"]))
        preset = a.get("preset")
        if getattr(self, "_shown_preset", object()) != preset:
            self._shown_preset = preset
            self._swatch_imgs = []
            for name, btn in self._swatch_btns.items():
                img = W.swatch(T.PRESETS[name], 38, selected=(preset == name))
                self._swatch_imgs.append(img)
                btn.configure(image=img)
        saved_state = (preset, tuple((t.get("name"), tuple(t.get("colors") or [])) for t in a.get("saved") or []))
        if getattr(self, "_shown_saved", None) != saved_state:
            self._shown_saved = saved_state
            self._render_mythemes()
        self.tint_label.configure(text=f"Насыщенность фона: {int(a.get('tint', 70))}%")
        colors = tuple(a.get("colors") or [])
        if getattr(self, "_shown_colors", None) != colors:
            self._shown_colors = colors
            self._render_custom()

    def on_palette(self) -> None:
        if getattr(self.app, "_color_picker", None) is not None:
            return  # пока открыт выбор цвета, обновим панель при его закрытии
        self._refresh_appearance_controls()

    def _mode_changed(self, label: str) -> None:
        self._appearance["mode"] = next((k for k, v in MODES.items() if v == label), "dark")
        self._save_and_apply()

    def _preset(self, name: str) -> None:
        self._appearance["preset"] = name
        self._appearance["colors"] = list(T.PRESETS[name])
        self._save_and_apply()

    # ------------------------------------------------------------ мои темы
    def _render_mythemes(self) -> None:
        for child in self.mythemes.winfo_children():
            child.destroy()
        a = self._appearance
        saved = a.get("saved") or []
        if not saved:
            ctk.CTkLabel(self.mythemes, text="Здесь появятся ваши темы: выберите цвета через «+ Цвет» и нажмите "
                                             "«Готово» — тема сохранится автоматически.",
                         anchor="w", justify="left", wraplength=640, text_color=W.P.muted,
                         font=ctk.CTkFont(size=11)).pack(fill="x")
            return
        self._my_imgs = []
        for i, theme in enumerate(saved):
            name = theme.get("name", "")
            cell = ctk.CTkFrame(self.mythemes, fg_color="transparent")
            cell.grid(row=i // 8, column=i % 8, padx=(0, 10), pady=(0, 6))
            img = W.swatch(T.normalize_colors(theme.get("colors")), 38,
                           selected=(a.get("preset") == T.MY_PREFIX + name))
            self._my_imgs.append(img)
            ctk.CTkButton(cell, text="", image=img, width=44, height=44, fg_color="transparent",
                          hover_color=W.P.surface2, corner_radius=22,
                          command=lambda i=i: self._apply_saved(i)).pack()
            line = ctk.CTkFrame(cell, fg_color="transparent")
            line.pack()
            label = ctk.CTkLabel(line, text=name, text_color=W.P.muted, font=ctk.CTkFont(size=11), cursor="hand2")
            label.pack(side="left")
            label.bind("<Double-Button-1>", lambda e, i=i, ln=line: self._rename_saved(i, ln))
            for text, cmd in (("✎", lambda i=i, ln=line: self._rename_saved(i, ln)),
                              ("✕", lambda i=i: self._delete_saved(i))):
                ctk.CTkButton(line, text=text, width=16, height=16, fg_color="transparent",
                              hover_color=W.P.surface2, text_color=W.P.muted, font=ctk.CTkFont(size=10),
                              command=cmd).pack(side="left", padx=(2, 0))

    def _apply_saved(self, index: int) -> None:
        saved = self._appearance.get("saved") or []
        if index < len(saved):
            self._appearance["colors"] = T.normalize_colors(saved[index].get("colors"))
            self._appearance["preset"] = T.MY_PREFIX + saved[index].get("name", "")
            self._save_and_apply()

    def _delete_saved(self, index: int) -> None:
        saved = self._appearance.get("saved") or []
        if index < len(saved):
            saved.pop(index)
            self._appearance["preset"] = T.preset_key_for(self._appearance)
            self.app.core.settings.save()
            self._refresh_appearance_controls()

    def _rename_saved(self, index: int, line) -> None:
        """Переименовать тему прямо на месте: Enter — сохранить, Esc — отмена."""
        saved = self._appearance.get("saved") or []
        if index >= len(saved):
            return
        old = saved[index].get("name", "")
        for child in line.winfo_children():
            child.pack_forget()
        entry = ctk.CTkEntry(line, width=110, height=24, font=ctk.CTkFont(size=11))
        entry.pack(side="left")
        entry.insert(0, old)
        entry.select_range(0, "end")
        entry.focus_set()
        done = {"v": False}

        def finish(save: bool) -> None:
            if done["v"]:
                return
            done["v"] = True
            new = entry.get().strip()[:24]
            if save and new and new != old:
                if any(t.get("name") == new for t in saved):
                    self.app.error(f"Тема «{new}» уже есть.")
                else:
                    saved[index]["name"] = new
                    if self._appearance.get("preset") == T.MY_PREFIX + old:
                        self._appearance["preset"] = T.MY_PREFIX + new
                    self.app.core.settings.save()
            self._shown_saved = None  # перерисовать раздел
            self._refresh_appearance_controls()

        entry.bind("<Return>", lambda e: finish(True))
        entry.bind("<FocusOut>", lambda e: finish(True))
        entry.bind("<Escape>", lambda e: finish(False))

    def _save_current(self) -> None:
        self._appearance["preset"] = T.remember_theme(self._appearance)
        self.app.core.settings.save()
        self._refresh_appearance_controls()

    def _tint_changed(self, value: float) -> None:
        self._appearance["tint"] = int(value)
        self.tint_label.configure(text=f"Насыщенность фона: {int(value)}%")
        if self._tint_job:
            self.after_cancel(self._tint_job)
        self._tint_job = self.after(350, self._tint_apply)

    def _tint_apply(self) -> None:
        self._tint_job = None
        self._save_and_apply()

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
        from .color_picker import ColorPicker
        picker = getattr(self.app, "_color_picker", None)
        if picker is not None and picker.winfo_exists():
            picker.lift()
            return

        def closed() -> None:
            self.app._color_picker = None
            self._refresh_appearance_controls()
        self.app._color_picker = ColorPicker(self.app, index, on_close=closed)

    def _remove_color(self, index: int) -> None:
        colors = list(self._appearance.get("colors") or [])
        if len(colors) > 1:
            colors.pop(index)
            self._appearance["colors"] = colors
            self._appearance["preset"] = T.preset_key_for(self._appearance)
            self._save_and_apply()

    # ------------------------------------------------------------ горячие клавиши
    @property
    def _hk(self) -> dict:
        return self.app.core.settings.data["hotkeys"]

    def _hk_render(self, failed=()) -> None:
        bindings = self._hk.get("bindings") or {}
        enabled = bool(self._hk.get("enabled"))
        for action, btn in self.hk_buttons.items():
            combo = bindings.get(action, "")
            if action == self._hk_capturing:
                btn.configure(text="Нажмите сочетание…", border_color=W.P.accent)
            else:
                btn.configure(text=combo or "не задано", border_color=W.P.border,
                              text_color=W.P.text if combo and enabled else W.P.muted)
        if failed:
            self.hk_info.configure(text="Заняты другой программой: " + ", ".join(failed) +
                                        ". Выберите другие сочетания.", text_color=W.RED)
        else:
            self.hk_info.configure(text="", text_color=W.P.muted)

    def _hk_apply(self) -> None:
        self.app.core.settings.save()
        failed = self.app.apply_hotkeys()
        self._hk_render(failed)

    def _hk_toggle(self) -> None:
        self._hk["enabled"] = bool(self.hk_switch.get())
        self._hk_apply()

    def _hk_set(self, action: str, combo: str) -> None:
        bindings = self._hk.setdefault("bindings", {})
        if combo:
            other = next((a for a, c in bindings.items() if c == combo and a != action), None)
            if other:
                self.app.error(f"{combo} уже назначено на «{hotkeys.ACTIONS[other]}».")
                return
        bindings[action] = combo
        self._hk_apply()

    def _hk_capture(self, action: str) -> None:
        if self._hk_capturing:
            return
        self._hk_capturing = action
        self.app.hotkeys.stop()  # иначе нажатие уже занятого сочетания перехватит Windows
        self._hk_render()
        self.app.bind("<KeyPress>", self._hk_key, add="+")
        self.app.focus_force()

    def _hk_key(self, event) -> str:
        action = self._hk_capturing
        if not action:
            return ""
        if event.keysym == "Escape":
            self._hk_finish()
            return "break"
        combo = hotkeys.combo_from_tk(event.state, event.keysym, event.keycode)
        if combo is None:
            if event.keysym not in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R"):
                self.hk_info.configure(text="Нужно сочетание с Ctrl или Alt, например Ctrl+Alt+Z",
                                       text_color=W.YELLOW)
            return "break"
        self._hk_finish()
        self._hk_set(action, combo)
        return "break"

    def _hk_finish(self) -> None:
        self._hk_capturing = None
        self.app.unbind("<KeyPress>")
        self._hk_apply()

    def _hk_defaults(self) -> None:
        self._hk["bindings"] = dict(hotkeys.DEFAULT_BINDINGS)
        self._hk_apply()

    # ------------------------------------------------------------ резервная копия
    def _export(self) -> None:
        import time
        path = filedialog.asksaveasfilename(
            parent=self.app, title="Сохранить настройки", defaultextension=".json",
            initialfile=f"BypassHub-settings-{time.strftime('%Y%m%d')}.json",
            filetypes=[("Настройки BypassHub", "*.json")])
        if not path:
            return
        core = self.app.core
        try:
            backup.export_file(Path(path), core.settings.data, core.zapret, core.tg)
        except Exception as exc:
            self.app.error(f"Не удалось сохранить: {exc}")
            return
        self.app.info(f"Настройки сохранены:\n{path}")

    def _import(self) -> None:
        path = filedialog.askopenfilename(parent=self.app, title="Загрузить настройки",
                                          filetypes=[("Настройки BypassHub", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        core = self.app.core

        def work():
            warnings = backup.import_file(Path(path), core.settings, core.zapret, core.tg)
            try:
                winutil.set_autostart(bool(core.settings.get("app", "autostart")))
            except Exception as exc:
                warnings.append(f"автозапуск: {exc}")
            core.zapret_apply()
            core.tg_apply()
            core.update_learner()
            return warnings

        def done(warnings, err):
            if err:
                self.app.error(f"Не удалось загрузить настройки: {err}")
                return
            self.app.apply_appearance()
            self._refresh_appearance_controls()
            self.app.apply_hotkeys()
            text = "Настройки загружены."
            if warnings:
                text += "\n\n" + "\n".join(warnings)
            self.app.info(text)
        self.app.run_task(work, done)

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
