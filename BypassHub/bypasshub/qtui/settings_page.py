"""Настройки: оформление, запуск, горячие клавиши, окно, данные, резервная копия, о программе."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QGridLayout, QHBoxLayout, QInputDialog, QPushButton, QSlider,
                               QVBoxLayout, QWidget, QFileDialog)

from .. import APP_NAME, __version__, backup, hotkeys, winutil
from .. import palette as T
from . import style as S
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from .app import App

MODES = {"dark": "Тёмная", "light": "Светлая", "system": "Как в Windows" if winutil.IS_WINDOWS else "Как в системе"}


class SettingsPage(Page):
    def __init__(self, app: "App"):
        super().__init__(app)
        s = app.core.settings
        W.page_title(self.lay, "Настройки", "Оформление, запуск и поведение программы")

        # ------------------------------------------------------------ оформление
        body = W.section(self.lay, "Оформление",
                         "Выберите тему и цвета — в них окрасится всё окно. Если выбрать несколько цветов, "
                         "фон станет плавным градиентом.")
        self.mode = W.row(body, "Тема", W.Segmented(MODES.values(), self._mode_changed))
        body.addWidget(W.label("Готовые наборы"))
        self.presets = QHBoxLayout()
        self.presets.setSpacing(10)
        body.addLayout(self.presets)
        head = W.hbox(W.label("Мои темы"), W.button("Сохранить текущие цвета", self._save_current), margins=(0, 8, 0, 0))
        body.addLayout(head)
        self.mythemes = QGridLayout()
        self.mythemes.setHorizontalSpacing(10)
        body.addLayout(self.mythemes)
        body.addWidget(W.label("Свои цвета (до трёх — градиент)"))
        self.custom = QHBoxLayout()
        self.custom.setSpacing(8)
        body.addLayout(self.custom)
        self.tint_label = W.label("")
        body.addSpacing(6)
        body.addWidget(self.tint_label)
        self.tint = QSlider(Qt.Orientation.Horizontal)
        self.tint.setRange(0, 100)
        self.tint.setValue(int(self._appearance.get("tint", 70)))
        self.tint.valueChanged.connect(self._tint_changed)
        body.addWidget(self.tint)
        self._tint_timer = QTimer(self)
        self._tint_timer.setSingleShot(True)
        self._tint_timer.timeout.connect(self._save_and_apply)
        self._shown = None
        self._refresh_appearance_controls()

        # ------------------------------------------------------------ запуск
        body = W.section(self.lay, "Запуск")
        self.autostart = W.Toggle("Запускать BypassHub при включении компьютера", self._autostart_changed)
        body.addWidget(self.autostart)
        if winutil.IS_LINUX:
            body.addWidget(W.label("Ярлык в ~/.config/autostart. Если при запуске нужно включить то, что требует прав "
                                   "администратора (zapret в режиме «Программа», VPN «Весь трафик»), система один раз "
                                   "спросит пароль. Режим zapret «Служба systemd» работает без пароля.",
                                   "small", wrap=True))
        else:
            body.addWidget(W.label("Через Планировщик заданий Windows — с правами администратора и без запроса UAC.",
                                   "small"))
        self._switch(body, "Запускать свёрнутым в трей", "start_minimized")
        self._switch(body, "При запуске включать то, что было включено (zapret, прокси)", "restore_state")

        # ------------------------------------------------------------ горячие клавиши
        self.hk_buttons: Dict[str, QPushButton] = {}
        self._hk_capturing: Optional[str] = None
        if winutil.IS_LINUX:
            self._linux_hotkeys()
        else:
            self._windows_hotkeys(s)

        # ------------------------------------------------------------ окно и выход
        self._build_rest(app)

    def _linux_hotkeys(self) -> None:
        """В Linux (особенно в Wayland) программа не может сама перехватывать сочетания клавиш —
        их назначают в системе на команду BypassHub --action …, которую выполнит запущенная копия."""
        from .. import linux
        body = W.section(self.lay, "Горячие клавиши",
                         "В Linux сочетания клавиш назначаются в системе. KDE Plasma: Параметры системы → "
                         "Клавиатура → Комбинации клавиш → «Добавить» → «Команда или сценарий». GNOME: "
                         "Настройки → Клавиатура → Комбинации клавиш → «Свои комбинации». Укажите команду:")
        base = linux.shell_join(linux.self_command())
        for action, text in hotkeys.ACTIONS.items():
            body.addWidget(W.label(text))
            body.addWidget(W.label(f"{base} --action {action}", "small", selectable=True, wrap=True))
        self.hk_info = W.label("", "small", wrap=True)

    def _windows_hotkeys(self, s) -> None:
        body = W.section(self.lay, "Горячие клавиши",
                         "Работают в любой программе, даже когда BypassHub свёрнут в трей. Нажмите на "
                         "сочетание, чтобы изменить его, затем нажмите новые клавиши (Esc — отмена). "
                         "Нужен хотя бы один из модификаторов Ctrl или Alt.")
        self.hk_switch = W.Toggle("Включить горячие клавиши", self._hk_toggle)
        W.set_checked(self.hk_switch, s.data["hotkeys"].get("enabled"))
        body.addWidget(self.hk_switch)
        for action, text in hotkeys.ACTIONS.items():
            btn = W.button("", lambda a=action: self._hk_capture(a), width=190)
            self.hk_buttons[action] = btn
            body.addLayout(W.hbox(W.label(text), None, btn,
                                  W.button("✕", lambda a=action: self._hk_set(a, ""), flat=True), stretch_last=False))
        self.hk_info = W.label("", "small", wrap=True)
        body.addWidget(self.hk_info)
        body.addLayout(W.button_bar(W.button("По умолчанию", self._hk_defaults)))
        self._key_filter = _KeyCatcher(self._hk_key)

    def _build_rest(self, app: "App") -> None:
        body = W.section(self.lay, "Окно и выход")
        self._switch(body, "Кнопка «закрыть» сворачивает в трей", "close_to_tray")
        self._switch(body, "При выходе выключать zapret (режим «Программа») и прокси", "stop_on_exit")

        body = W.section(self.lay, "Данные")
        body.addWidget(W.label(f"Папка: {app.core.paths.root}", selectable=True))
        body.addWidget(W.label("Старые версии zapret и tg-ws-proxy удаляются сразу после обновления, "
                               "временные файлы загрузки — при каждом запуске.", "small", wrap=True))
        body.addLayout(W.button_bar(W.button("Открыть папку", lambda: winutil.open_path(app.core.paths.root))))

        body = W.section(self.lay, "Резервная копия настроек",
                         "Все настройки в одном файле: BypassHub, zapret (Game Filter, IPSet, фейки, списки, "
                         "исключения) и TG WS Proxy. Удобно для переноса на другой компьютер. В файле есть "
                         "secret прокси — не публикуйте его.")
        body.addLayout(W.button_bar(W.button("Экспорт", self._export, primary=True), W.button("Импорт", self._import)))

        body = W.section(self.lay, "О программе")
        body.addWidget(W.label(f"{APP_NAME} {__version__} — zapret-discord-youtube, tg-ws-proxy и свой VPN "
                               "(VLESS, AmneziaWG) в одном окне.", wrap=True))
        body.addWidget(W.label("Сами проекты принадлежат их авторам (Flowseal, bol-van, SagerNet, Amnezia). "
                               "BypassHub скачивает только официальные релизы с GitHub.", "muted", wrap=True))
        body.addLayout(W.button_bar(
            W.button("zapret-discord-youtube", lambda: winutil.open_url("https://github.com/Flowseal/zapret-discord-youtube")),
            W.button("tg-ws-proxy", lambda: winutil.open_url("https://github.com/Flowseal/tg-ws-proxy"))))
        self.finish()
        self.on_show()

    # ------------------------------------------------------------ оформление
    @property
    def _appearance(self) -> dict:
        return self.app.core.settings.data["appearance"]

    def _save_and_apply(self) -> None:
        self.app.core.settings.save()
        self.app.apply_appearance()
        self._refresh_appearance_controls()

    def on_palette(self) -> None:
        self._shown = None
        self._refresh_appearance_controls()

    def _refresh_appearance_controls(self) -> None:
        """Обновить элементы «Оформления», если что-то изменилось."""
        a = self._appearance
        self.mode.set(MODES.get(a.get("mode"), MODES["dark"]))
        state = (a.get("preset"), tuple((t.get("name"), tuple(t.get("colors") or [])) for t in a.get("saved") or []),
                 tuple(a.get("colors") or []))
        W.set_text(self.tint_label, f"Насыщенность фона: {int(a.get('tint', 70))}%")
        if state == self._shown:
            return
        self._shown = state
        preset = a.get("preset")
        W.clear_layout(self.presets)
        for name, colors in T.PRESETS.items():
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(W.swatch_button(colors, 38, preset == name, lambda n=name: self._preset(n)),
                           0, Qt.AlignmentFlag.AlignHCenter)
            cap = W.label(name, "small")
            cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            cell.addWidget(cap)
            self.presets.addLayout(cell)
        self.presets.addStretch(1)
        self._render_mythemes()
        self._render_custom()

    def _mode_changed(self, text: str) -> None:
        self._appearance["mode"] = next((k for k, v in MODES.items() if v == text), "dark")
        self._save_and_apply()

    def _preset(self, name: str) -> None:
        self._appearance["preset"] = name
        self._appearance["colors"] = list(T.PRESETS[name])
        self._save_and_apply()

    # ------------------------------------------------------------ мои темы
    def _render_mythemes(self) -> None:
        W.clear_layout(self.mythemes)
        a = self._appearance
        saved = a.get("saved") or []
        if not saved:
            self.mythemes.addWidget(W.label("Здесь появятся ваши темы: выберите цвета через «+ Цвет» — тема "
                                            "сохранится автоматически.", "small", wrap=True), 0, 0)
            return
        for i, theme in enumerate(saved):
            name = theme.get("name", "")
            cell = QVBoxLayout()
            cell.setSpacing(1)
            cell.addWidget(W.swatch_button(T.normalize_colors(theme.get("colors")), 38,
                                           a.get("preset") == T.MY_PREFIX + name, lambda i=i: self._apply_saved(i)),
                           0, Qt.AlignmentFlag.AlignHCenter)
            line = QHBoxLayout()
            line.setSpacing(0)
            line.addWidget(W.label(name, "small"))
            line.addWidget(W.button("✎", lambda i=i: self._rename_saved(i), flat=True))
            line.addWidget(W.button("✕", lambda i=i: self._delete_saved(i), flat=True))
            cell.addLayout(line)
            holder = QWidget()
            holder.setLayout(cell)
            self.mythemes.addWidget(holder, i // 8, i % 8)
        self.mythemes.setColumnStretch(8, 1)

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

    def _rename_saved(self, index: int) -> None:
        saved = self._appearance.get("saved") or []
        if index >= len(saved):
            return
        old = saved[index].get("name", "")
        new, ok = QInputDialog.getText(self, "Переименовать тему", "Название темы:", text=old)
        new = new.strip()[:24]
        if not ok or not new or new == old:
            return
        if any(t.get("name") == new for t in saved):
            self.app.error(f"Тема «{new}» уже есть.")
            return
        saved[index]["name"] = new
        if self._appearance.get("preset") == T.MY_PREFIX + old:
            self._appearance["preset"] = T.MY_PREFIX + new
        self.app.core.settings.save()
        self._refresh_appearance_controls()

    def _save_current(self) -> None:
        self._appearance["preset"] = T.remember_theme(self._appearance)
        self.app.core.settings.save()
        self._refresh_appearance_controls()

    def _tint_changed(self, value: int) -> None:
        self._appearance["tint"] = int(value)
        W.set_text(self.tint_label, f"Насыщенность фона: {int(value)}%")
        self._tint_timer.start(300)

    def _render_custom(self) -> None:
        W.clear_layout(self.custom)
        colors: List[str] = list(self._appearance.get("colors") or [])
        for i, color in enumerate(colors):
            cell = W.Card(name="row", margins=(6, 4, 4, 4), spacing=0)
            line = QHBoxLayout()
            line.setSpacing(4)
            line.addWidget(W.swatch_button([color], 26, False, lambda i=i: self._pick(i)))
            line.addWidget(W.button(color, lambda i=i: self._pick(i), flat=True))
            if len(colors) > 1:
                line.addWidget(W.button("✕", lambda i=i: self._remove_color(i), flat=True))
            cell.body.addLayout(line)
            self.custom.addWidget(cell)
        if len(colors) < 3:
            self.custom.addWidget(W.button("+ Цвет", lambda: self._pick(len(colors))))
        self.custom.addStretch(1)

    def _pick(self, index: int) -> None:
        """Выбор цвета: окно сразу показывает, как будет выглядеть программа."""
        colors = list(self._appearance.get("colors") or [])
        before = list(colors)
        start = QColor(colors[index]) if index < len(colors) else QColor(S.P.accent)
        dlg = QColorDialog(start, self)
        dlg.setWindowTitle("Цвет оформления")

        def preview(c: QColor) -> None:
            if not c.isValid():
                return
            cols = list(before)
            if index < len(cols):
                cols[index] = c.name()
            else:
                cols.append(c.name())
            self._appearance["colors"] = cols
            self.app.apply_appearance()
        dlg.currentColorChanged.connect(preview)
        if dlg.exec():
            preview(dlg.selectedColor())
            self._appearance["preset"] = T.remember_theme(self._appearance)
        else:
            self._appearance["colors"] = before
        self._save_and_apply()

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

    def hk_render(self, failed=()) -> None:
        bindings = self._hk.get("bindings") or {}
        enabled = bool(self._hk.get("enabled"))
        for action, btn in self.hk_buttons.items():
            combo = bindings.get(action, "")
            if action == self._hk_capturing:
                btn.setText("Нажмите сочетание…")
                btn.setStyleSheet(f"border-color: {S.P.accent};")
            else:
                btn.setText(combo or "не задано")
                btn.setStyleSheet("" if combo and enabled else f"color: {S.P.muted};")
        if failed:
            self.hk_info.setText("Заняты другой программой: " + ", ".join(failed) + ". Выберите другие сочетания.")
            W.set_label_color(self.hk_info, S.red())
        else:
            self.hk_info.setText("")

    def _hk_apply(self) -> None:
        self.app.core.settings.save()
        self.hk_render(self.app.apply_hotkeys())

    def _hk_toggle(self, on: bool) -> None:
        self._hk["enabled"] = on
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
        self.hk_render()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self._key_filter)

    def _hk_key(self, event) -> bool:
        action = self._hk_capturing
        if not action:
            return False
        if event.key() == Qt.Key.Key_Escape:
            self._hk_finish()
            return True
        mods = event.modifiers()
        combo = hotkeys.combo_from_qt(bool(mods & Qt.KeyboardModifier.ControlModifier),
                                      bool(mods & Qt.KeyboardModifier.AltModifier),
                                      bool(mods & Qt.KeyboardModifier.ShiftModifier),
                                      int(event.key()), int(event.nativeVirtualKey()))
        if combo is None:
            if event.key() not in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta):
                self.hk_info.setText("Нужно сочетание с Ctrl или Alt, например Ctrl+Alt+Z")
                W.set_label_color(self.hk_info, S.yellow())
            return True
        self._hk_finish()
        self._hk_set(action, combo)
        return True

    def _hk_finish(self) -> None:
        self._hk_capturing = None
        from PySide6.QtWidgets import QApplication
        QApplication.instance().removeEventFilter(self._key_filter)
        self._hk_apply()

    def _hk_defaults(self) -> None:
        self._hk["bindings"] = dict(hotkeys.DEFAULT_BINDINGS)
        self._hk_apply()

    # ------------------------------------------------------------ резервная копия
    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить настройки",
                                              f"BypassHub-settings-{time.strftime('%Y%m%d')}.json",
                                              "Настройки BypassHub (*.json)")
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
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить настройки", "",
                                              "Настройки BypassHub (*.json);;Все файлы (*.*)")
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
            self._shown = None
            self._refresh_appearance_controls()
            self.app.apply_hotkeys()
            text = "Настройки загружены."
            if warnings:
                text += "\n\n" + "\n".join(warnings)
            self.app.info(text)
        self.app.run_task(work, done)

    # ------------------------------------------------------------ запуск
    def _switch(self, layout, text: str, key: str) -> W.Toggle:
        s = self.app.core.settings
        sw = W.Toggle(text, lambda on: s.set("app", key, on))
        W.set_checked(sw, s.get("app", key))
        layout.addWidget(sw)
        return sw

    def on_show(self) -> None:
        def done(enabled, err):
            if not err:
                W.set_checked(self.autostart, enabled)
        self.app.run_task(winutil.autostart_enabled, done)

    def _autostart_changed(self, on: bool) -> None:
        def work():
            winutil.set_autostart(on)
            self.app.core.settings.set("app", "autostart", on)

        def done(_, err):
            if err:
                W.set_checked(self.autostart, not on)
                self.app.error(f"Не удалось изменить автозапуск: {err}")
        self.app.run_task(work, done)


class _KeyCatcher(QObject):
    """Перехват нажатий во всём приложении, пока назначается горячая клавиша."""

    def __init__(self, handler):
        super().__init__()
        self.handler = handler

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — имя Qt
        if event.type() == QEvent.Type.KeyPress:
            return self.handler(event)
        return False
