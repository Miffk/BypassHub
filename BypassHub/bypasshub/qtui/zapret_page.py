"""Zapret — всё, что умеет service.bat, но кнопками."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .. import winutil
from ..tgproxy import TgStatus
from ..zapret import APP_EXCLUSIONS, FAKE_TARGETS, USER_LISTS, GameFilter, ZapretStatus, validate_ports
from . import style as S
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from .app import App

GAME_LABELS = {"disabled": "Выключен", "all": "TCP и UDP", "tcp": "Только TCP", "udp": "Только UDP"}
IPSET_LABELS = {"none": "none", "loaded": "loaded", "any": "any"}
MODE_LABELS = {"process": "Программа (winws.exe)", "service": "Служба Windows"}


class ZapretPage(Page):
    def __init__(self, app: "App"):
        super().__init__(app)
        from .home import ServiceCard
        home = app.pages["home"][1]
        self.header = ServiceCard("Zapret", "Обход блокировок Discord, YouTube и других сервисов "
                                            "(winws.exe + WinDivert)", home.toggle_zapret)
        self.header.add_buttons(W.button("Подобрать стратегию", self._auto_pick, primary=True),
                                W.button("Проверить доступность", lambda: home.check_services()))
        self.lay.addWidget(self.header)
        home.zapret_card.add_header(self.header)
        self.not_installed = W.label("zapret ещё не загружен. Он скачается автоматически, или нажмите «Проверить "
                                     "сейчас» во вкладке «Обновления».", wrap=True)
        W.set_label_color(self.not_installed, S.yellow())
        self.lay.addWidget(self.not_installed)

        # --- стратегия и режим
        body = W.section(self.lay, "Стратегия и режим запуска",
                         "«Программа» — winws.exe работает, пока запущен BypassHub. «Служба Windows» — как "
                         "Install Service в service.bat: обход работает сам с момента включения ПК.")
        self.strategy_box = W.row(body, "Стратегия", W.combo(on_change=self._strategy_changed))
        self.mode_btn = W.row(body, "Режим", W.Segmented(MODE_LABELS.values(), self._mode_changed))
        self.service_label = W.label("", "muted")
        body.addWidget(self.service_label)

        # --- Game Filter
        body = W.section(self.lay, "Game Filter",
                         "Обход для игр и других сервисов, использующих TCP/UDP на портах выше 1023. "
                         "По умолчанию выключен — включайте, только если не работает игра.")
        self.game_mode = W.row(body, "Режим", W.Segmented(GAME_LABELS.values()))
        self.game_tcp = W.row(body, "Порты TCP", W.entry("1024-65535"))
        self.game_udp = W.row(body, "Порты UDP", W.entry("1024-65535"),
                              hint="Формат: 1024-65535 или несколько через запятую: 1024-1934,1936-65535")
        body.addLayout(W.button_bar(W.button("Сохранить", self._save_game_filter, primary=True)))

        # --- IPSet
        body = W.section(self.lay, "IPSet Filter",
                         "none — IP-адреса не проверяются; loaded — обход для IP из списка ipset-all.txt; "
                         "any — обход для любых IP. Если перестало работать то, что работает без zapret, "
                         "поставьте none.")
        self.ipset_btn = W.row(body, "Режим", W.Segmented(IPSET_LABELS.values(), self._ipset_changed))
        body.addLayout(W.button_bar(W.button("Обновить IPSet-список", self._update_ipset, primary=True)))

        # --- фейки
        body = W.section(self.lay, "Активные фейки",
                         "Какой файл из папки bin подставляется в стратегии как ACTIVE_*.bin "
                         "(аналог «Replace active fakes»).")
        self.fake_boxes = {}
        for kind, (_, text) in FAKE_TARGETS.items():
            self.fake_boxes[kind] = W.row(body, text, W.combo(on_change=lambda v, k=kind: self._fake_changed(k, v)))

        # --- исключения программ
        body = W.section(self.lay, "Не применять обход к программам",
                         "Отметьте программы, которые должны работать без zapret (например, если Steam с "
                         "включённым обходом грузит медленнее). winws работает на уровне сетевых пакетов и не "
                         "видит, какой программе они принадлежат, поэтому исключаются домены этих программ. "
                         "Если включён Game Filter, трафик игр по IP всё равно может попадать под обход.")
        grid = W.grid(3)
        self.app_checks = {}
        for i, name in enumerate(APP_EXCLUSIONS):
            cb = W.checkbox(name)
            grid.addWidget(cb, i // 3, i % 3)
            self.app_checks[name] = cb
        body.addLayout(grid)
        self.app_custom = W.row(body, "Свои домены (через запятую)", W.entry("example.com, game.net"))
        body.addLayout(W.button_bar(W.button("Применить", self._apps_changed, primary=True)))
        self.learn_sw = W.Toggle("Экспериментально: также исключать IP-адреса, к которым подключаются отмеченные "
                                 "программы", self._learn_changed)
        body.addSpacing(6)
        body.addWidget(self.learn_sw)
        self.learn_info = W.label("", "small", wrap=True)
        body.addWidget(self.learn_info)
        body.addLayout(W.button_bar(W.button("Очистить выученные адреса", self._learn_clear)))

        # --- пользовательские списки
        body = W.section(self.lay, "Пользовательские списки",
                         "Свои домены и исключения. Файлы сохраняются при обновлении zapret.")
        self.tabs = QTabWidget()
        self.list_boxes = {}
        for name, (desc, _) in USER_LISTS.items():
            tab = QWidget()
            tl = QVBoxLayout(tab)
            tl.setContentsMargins(0, 8, 0, 0)
            tl.addWidget(W.label(desc, "muted", wrap=True))
            box = W.textbox(150)
            tl.addWidget(box)
            tl.addLayout(W.button_bar(W.button("Сохранить", lambda n=name: self._save_list(n), primary=True)))
            self.tabs.addTab(tab, name)
            self.list_boxes[name] = box
        body.addWidget(self.tabs)

        # --- hosts
        body = W.section(self.lay, "Файл hosts",
                         "Нужен для веб-версии Telegram и голосовых каналов Discord (аналог «Update Hosts File»). "
                         "Записи добавляются отдельным блоком, перед изменением создаётся копия hosts.bypasshub.bak.")
        self.hosts_label = W.label("Статус не проверялся")
        body.addWidget(self.hosts_label)
        body.addLayout(W.button_bar(W.button("Проверить", self._hosts_check, primary=True),
                                    W.button("Обновить автоматически", self._hosts_apply, primary=True),
                                    W.button("Вручную (блокнот)", self._hosts_manual),
                                    W.button("Удалить записи", self._hosts_remove)))

        # --- инструменты
        body = W.section(self.lay, "Инструменты")
        body.addLayout(W.button_bar(W.button("Автоподбор стратегии", self._auto_pick, primary=True),
                                    W.button("Диагностика", self._diagnostics, primary=True),
                                    W.button("Удалить службы", self._remove_services)))
        body.addLayout(W.button_bar(W.button("Открыть папку zapret", lambda: winutil.open_path(app.core.zapret.root)),
                                    W.button("Лог winws.exe", self._open_winws_log),
                                    W.button("Показать команду", self._show_command),
                                    W.button("Тест в PowerShell", self._run_tests)))
        self.finish()
        self.sync_from_settings()
        self.reload()

    # ------------------------------------------------------------------ загрузка значений
    def on_show(self) -> None:
        self.reload()

    def sync_from_settings(self) -> None:
        self.mode_btn.set(MODE_LABELS[self.app.core.zapret_mode])

    def reload(self) -> None:
        zm = self.app.core.zapret
        installed = zm.is_installed()
        self.not_installed.setVisible(not installed)
        names = zm.strategies()
        cur = self.app.core.strategy
        W.set_combo(self.strategy_box, names or ["—"], cur if cur in names else (names[0] if names else "—"))

        gf = zm.load_game_filter()
        self.game_mode.set(GAME_LABELS.get(gf.mode, GAME_LABELS["disabled"]))
        self.game_tcp.setText(gf.tcp)
        self.game_udp.setText(gf.udp)
        self.ipset_btn.set(zm.ipset_status() if installed else "")

        fakes = zm.fake_files()
        active = zm.active_fakes() if installed else {}
        for kind, box in self.fake_boxes.items():
            values = list(fakes or ["—"])
            cur = active.get(kind) or "(свой файл)"
            if cur not in values:
                values = [cur] + values
            W.set_combo(box, values, cur)

        zm.ensure_user_lists()
        names, custom = zm.app_exclusions() if installed else ([], [])
        for name, cb in self.app_checks.items():
            W.set_checked(cb, name in names)
        self.app_custom.setText(", ".join(custom))
        W.set_checked(self.learn_sw, self.app.core.settings.get("zapret", "learn_apps"))
        self._update_learn_info()
        for name, box in self.list_boxes.items():
            text = zm.read_user_list(name)
            if box.toPlainText() != text:
                box.setPlainText(text)

    def on_status(self, zs: ZapretStatus, ts: TgStatus) -> None:
        if zs.service_installed:
            text = f"Служба zapret: {zs.service_state}"
            if zs.service_strategy:
                text += f" · стратегия {zs.service_strategy}"
        else:
            text = "Служба zapret не установлена"
        W.set_text(self.service_label, text + " · winws.exe " + ("запущен" if zs.running else "не запущен"))

    # ------------------------------------------------------------------ helpers
    def _after_change(self, what: str) -> None:
        """Перезапуск обхода, если он работает, чтобы изменения вступили в силу."""
        home = self.app.pages["home"][1]

        def done(_, err):
            home.zapret_card.set_busy(False)
            if err:
                self.app.error(str(err), "Zapret")
        st = self.app.zapret_status
        if st.running or st.service_installed:
            home.zapret_card.set_busy(True, f"Применение: {what}…")
            self.app.run_task(self.app.core.zapret_apply, done)

    # ------------------------------------------------------------------ обработчики
    def _strategy_changed(self, value: str) -> None:
        if value == "—":
            return
        self.app.core.settings.set("zapret", "strategy", value)
        self._after_change("стратегия")

    def _mode_changed(self, text: str) -> None:
        self.app.core.settings.set("zapret", "mode", "service" if text == MODE_LABELS["service"] else "process")
        self._after_change("режим")

    def _save_game_filter(self) -> None:
        mode = next((k for k, v in GAME_LABELS.items() if v == self.game_mode.get()), "disabled")
        tcp = validate_ports(self.game_tcp.text())
        udp = validate_ports(self.game_udp.text())
        if tcp is None or udp is None:
            self.app.error("Неверный диапазон портов. Пример: 1024-65535 или 1024-1934,1936-65535")
            return
        self.app.core.zapret.save_game_filter(GameFilter(mode, tcp, udp))
        self._after_change("Game Filter")
        if not (self.app.zapret_status.running or self.app.zapret_status.service_installed):
            self.app.info("Game Filter сохранён.")

    def _ipset_changed(self, mode: str) -> None:
        zm = self.app.core.zapret
        try:
            zm.set_ipset_mode(mode)
        except FileNotFoundError:
            if self.app.ask("Сохранённого списка IP нет. Скачать актуальный ipset-all.txt?"):
                self._update_ipset()
            else:
                self.ipset_btn.set(zm.ipset_status())
        except Exception as exc:
            self.app.error(str(exc))
            self.ipset_btn.set(zm.ipset_status())

    def _update_ipset(self) -> None:
        def done(count, err):
            if err:
                self.app.error(f"Не удалось обновить список: {err}")
            else:
                self.ipset_btn.set(self.app.core.zapret.ipset_status())
                self.app.info(f"IPSet-список обновлён ({count} записей). Режим: loaded.")
        self.app.run_task(self.app.core.zapret.update_ipset_list, done)

    def _fake_changed(self, kind: str, value: str) -> None:
        if value in ("—", "(свой файл)"):
            return
        try:
            self.app.core.zapret.set_active_fake(kind, value)
        except Exception as exc:
            self.app.error(str(exc))
            return
        self._after_change("фейк")

    def _apps_changed(self) -> None:
        zm = self.app.core.zapret
        if not zm.is_installed():
            self.app.error("zapret ещё не загружен")
            return
        names = [n for n, cb in self.app_checks.items() if cb.isChecked()]
        custom = [d for d in self.app_custom.text().replace(";", ",").replace(" ", ",").split(",") if d.strip()]
        zm.set_app_exclusions(names, custom)
        self.list_boxes["list-exclude-user.txt"].setPlainText(zm.read_user_list("list-exclude-user.txt"))
        self.app.core.update_learner()
        self._update_learn_info()
        self.app.info("Исключения применены. Перезапуск обхода не нужен — zapret сам перечитывает списки.")

    def _learn_changed(self, on: bool) -> None:
        self.app.core.settings.set("zapret", "learn_apps", on)
        self.app.core.update_learner()
        self._update_learn_info()

    def _learn_clear(self) -> None:
        self.app.core.learner.clear()
        self.list_boxes["ipset-exclude-user.txt"].setPlainText(
            self.app.core.zapret.read_user_list("ipset-exclude-user.txt"))
        self._update_learn_info()

    def _update_learn_info(self) -> None:
        count = len(self.app.core.learner.learned())
        W.set_text(self.learn_info, "Раз в 8 секунд программа смотрит, к каким адресам подключены отмеченные программы "
                                    "(только локальная таблица соединений — сеть не нагружается), и добавляет их в "
                                    "ipset-exclude-user.txt. Первое подключение к новому адресу ещё идёт через обход. "
                                    f"Адреса Discord и YouTube не исключаются. Выучено адресов: {count}.")

    def _auto_pick(self) -> None:
        if not self.app.core.zapret.is_installed():
            self.app.error("zapret ещё не загружен")
            return
        from .dialogs import StrategyDialog
        StrategyDialog(self.app).show()

    def _save_list(self, name: str) -> None:
        zm = self.app.core.zapret
        zm.write_user_list(name, self.list_boxes[name].toPlainText())
        self.list_boxes[name].setPlainText(zm.read_user_list(name))
        self.app.info(f"{name} сохранён. zapret подхватит изменения сам, без перезапуска.")

    # ------------------------------------------------------------------ hosts
    def _hosts_status(self, text: str, color: str) -> None:
        self.hosts_label.setText(text)
        W.set_label_color(self.hosts_label, color)

    def _hosts_check(self) -> None:
        def done(res, err):
            if err:
                self._hosts_status(f"Ошибка проверки: {err}", S.red())
                return
            needs, _ = res
            self._hosts_status("hosts нужно обновить" if needs else "hosts актуален",
                               S.yellow() if needs else S.green())
        self.app.run_task(self.app.core.zapret.hosts_check, done)

    def _hosts_apply(self) -> None:
        if not self.app.ask("Добавить в hosts актуальные записи из репозитория zapret-discord-youtube?\n\n"
                            "Некоторые антивирусы реагируют на изменение hosts — в этом случае используйте "
                            "ручной вариант."):
            return
        zm = self.app.core.zapret

        def work():
            _, block = zm.hosts_check()
            zm.hosts_apply(block)

        def done(_, err):
            if err:
                self.app.error(f"Не удалось изменить hosts: {err}\n\nПопробуйте ручной вариант.")
            else:
                self._hosts_status("hosts обновлён", S.green())
                self.app.info("hosts обновлён.")
        self.app.run_task(work, done)

    def _hosts_manual(self) -> None:
        zm = self.app.core.zapret

        def work():
            _, block = zm.hosts_check()
            tmp = self.app.core.paths.downloads / "zapret_hosts.txt"
            tmp.write_text(block, encoding="utf-8")
            return tmp

        def done(tmp, err):
            if err:
                self.app.error(str(err))
                return
            if winutil.IS_WINDOWS:
                import subprocess
                subprocess.Popen(["notepad", str(tmp)])
                subprocess.Popen(["explorer", "/select,", str(zm.hosts_path())])
            self.app.info("Скопируйте текст из блокнота в конец файла hosts (редактор нужно открыть от имени "
                          "администратора) и сохраните его.")
        self.app.run_task(work, done)

    def _hosts_remove(self) -> None:
        def done(removed, err):
            if err:
                self.app.error(str(err))
            else:
                self.app.info("Записи удалены." if removed else "Записей BypassHub в hosts нет.")
        self.app.run_task(self.app.core.zapret.hosts_remove, done)

    # ------------------------------------------------------------------ инструменты
    def _diagnostics(self) -> None:
        from .. import zapret_diag
        from .dialogs import DiagnosticsDialog
        root = self.app.core.zapret.root
        self.app.run_task(lambda: zapret_diag.run_diagnostics(root),
                          lambda res, err: self.app.error(str(err)) if err else DiagnosticsDialog(self.app, res).show())

    def _run_tests(self) -> None:
        if not self.app.core.zapret.is_installed():
            self.app.error("zapret ещё не загружен")
            return
        if not self.app.ask("Расширенный тест из zapret-discord-youtube (в том числе DPI checkers). Текущий обход "
                            "будет выключен, откроется окно PowerShell — следуйте его инструкциям. Для обычного "
                            "подбора удобнее кнопка «Автоподбор стратегии».\n\nНачать?"):
            return

        def work():
            self.app.core.zapret_disable()
            self.app.core.zapret.run_tests()
        self.app.run_task(work, lambda r, e: self.app.error(str(e)) if e else None)

    def _remove_services(self) -> None:
        if not self.app.ask("Остановить обход и удалить службы zapret и WinDivert?"):
            return

        def work():
            self.app.core.settings.set("zapret", "enabled", False)
            self.app.core.zapret.remove_services()
        self.app.run_task(work, lambda r, e: self.app.error(str(e)) if e else self.app.info("Службы удалены."))

    def _open_winws_log(self) -> None:
        p = self.app.core.paths.winws_log
        if p.exists():
            winutil.open_path(p)
        else:
            self.app.info("Лог появится после запуска в режиме «Программа».")

    def _show_command(self) -> None:
        try:
            cmd = self.app.core.zapret.command_line(self.app.core.strategy)
        except Exception as exc:
            self.app.error(str(exc))
            return
        from .dialogs import TextDialog
        TextDialog(self.app, "Команда запуска winws.exe", cmd.replace(" --new ", " --new" + os.linesep)).show()
