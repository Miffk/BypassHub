from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict

import customtkinter as ctk

from .. import winutil, zapret_diag
from ..tgproxy import TgStatus
from ..zapret import APP_EXCLUSIONS, FAKE_TARGETS, USER_LISTS, GameFilter, ZapretStatus, validate_ports
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

GAME_LABELS = {"disabled": "Выключен", "all": "TCP и UDP", "tcp": "Только TCP", "udp": "Только UDP"}
IPSET_LABELS = {"none": "none", "loaded": "loaded", "any": "any"}
MODE_LABELS = {"process": "Программа (winws.exe)", "service": "Служба Windows"}


class ZapretPage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, fg_color=W.P.window_bg, scrollbar_button_color=W.P.border)
        self.app = app
        W.page_title(self, "Zapret", "Обход блокировок Discord, YouTube и других сервисов")
        self.not_installed = ctk.CTkLabel(self, text="zapret ещё не загружен. Он скачается автоматически, "
                                                     "или нажмите «Проверить сейчас» во вкладке «Обновления».",
                                          text_color=W.YELLOW, anchor="w", wraplength=680, justify="left")

        # --- стратегия и режим
        body = W.section(self, "Стратегия и режим запуска",
                         "«Программа» — winws.exe работает, пока запущен BypassHub. «Служба Windows» — как "
                         "Install Service в service.bat: обход работает сам с момента включения ПК.")
        self.strategy_box = W.row(body, "Стратегия", lambda p: ctk.CTkComboBox(
            p, values=[], state="readonly", command=self._strategy_changed))
        self.mode_btn = W.row(body, "Режим", lambda p: ctk.CTkSegmentedButton(
            p, values=list(MODE_LABELS.values()), command=self._mode_changed))
        self.service_label = ctk.CTkLabel(body, text="", anchor="w", text_color=W.GRAY)
        self.service_label.pack(fill="x", pady=(4, 0))

        # --- Game Filter
        body = W.section(self, "Game Filter",
                         "Обход для игр и других сервисов, использующих TCP/UDP на портах выше 1023. "
                         "По умолчанию выключен — включайте, только если не работает игра.")
        self.game_mode = W.row(body, "Режим", lambda p: ctk.CTkSegmentedButton(
            p, values=list(GAME_LABELS.values())))
        self.game_tcp = W.row(body, "Порты TCP", lambda p: ctk.CTkEntry(p, placeholder_text="1024-65535"))
        self.game_udp = W.row(body, "Порты UDP", lambda p: ctk.CTkEntry(p, placeholder_text="1024-65535"),
                              hint="Формат: 1024-65535 или несколько через запятую: 1024-1934,1936-65535")
        bar = W.button_bar(body)
        W.add_button(bar, "Сохранить", self._save_game_filter)

        # --- IPSet
        body = W.section(self, "IPSet Filter",
                         "none — IP-адреса не проверяются; loaded — обход для IP из списка ipset-all.txt; "
                         "any — обход для любых IP. Если перестало работать то, что работает без zapret, "
                         "поставьте none.")
        self.ipset_btn = W.row(body, "Режим", lambda p: ctk.CTkSegmentedButton(
            p, values=list(IPSET_LABELS.values()), command=self._ipset_changed))
        bar = W.button_bar(body)
        W.add_button(bar, "Обновить IPSet-список", self._update_ipset, width=200)

        # --- фейки
        body = W.section(self, "Активные фейки",
                         "Какой файл из папки bin подставляется в стратегии как ACTIVE_*.bin "
                         "(аналог «Replace active fakes»).")
        self.fake_boxes: Dict[str, ctk.CTkComboBox] = {}
        for kind, (_, label) in FAKE_TARGETS.items():
            self.fake_boxes[kind] = W.row(body, label, lambda p, k=kind: ctk.CTkComboBox(
                p, values=[], state="readonly", command=lambda v, k=k: self._fake_changed(k, v)))

        # --- исключения программ
        body = W.section(self, "Не применять обход к программам",
                         "Отметьте программы, которые должны работать без zapret (например, если Steam с "
                         "включённым обходом грузит медленнее). winws работает на уровне сетевых пакетов и не "
                         "видит, какой программе они принадлежат, поэтому исключаются домены этих программ. "
                         "Если включён Game Filter, трафик игр по IP всё равно может попадать под обход.")
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x")
        self.app_checks: Dict[str, ctk.CTkCheckBox] = {}
        for i, name in enumerate(APP_EXCLUSIONS):
            cb = ctk.CTkCheckBox(grid, text=name)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 24), pady=4)
            self.app_checks[name] = cb
        self.app_custom = W.row(body, "Свои домены (через запятую)",
                                lambda p: ctk.CTkEntry(p, placeholder_text="example.com, game.net"))
        bar = W.button_bar(body)
        W.add_button(bar, "Применить", self._apps_changed)
        self.learn_sw = ctk.CTkSwitch(body, text="Экспериментально: также исключать IP-адреса, к которым "
                                                "подключаются отмеченные программы", command=self._learn_changed)
        self.learn_sw.pack(anchor="w", pady=(12, 0))
        self.learn_info = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=640,
                                       text_color=W.P.muted, font=ctk.CTkFont(size=11))
        self.learn_info.pack(fill="x")
        bar = W.button_bar(body)
        W.add_button(bar, "Очистить выученные адреса", self._learn_clear, secondary=True, width=220)

        # --- пользовательские списки
        body = W.section(self, "Пользовательские списки",
                         "Свои домены и исключения. Файлы сохраняются при обновлении zapret.")
        self.tabs = ctk.CTkTabview(body, height=230)
        self.tabs.pack(fill="x")
        self.list_boxes: Dict[str, ctk.CTkTextbox] = {}
        for name, (desc, _) in USER_LISTS.items():
            tab = self.tabs.add(name)
            ctk.CTkLabel(tab, text=desc, anchor="w", text_color=W.GRAY).pack(fill="x")
            box = ctk.CTkTextbox(tab, height=140, font=ctk.CTkFont(family="Consolas", size=12))
            box.pack(fill="both", expand=True, pady=4)
            self.list_boxes[name] = box
            bar = W.button_bar(tab)
            W.add_button(bar, "Сохранить", lambda n=name: self._save_list(n))

        # --- hosts
        body = W.section(self, "Файл hosts",
                         "Нужен для веб-версии Telegram и голосовых каналов Discord (аналог «Update Hosts File»). "
                         "Записи добавляются отдельным блоком, перед изменением создаётся копия hosts.bypasshub.bak.")
        self.hosts_label = ctk.CTkLabel(body, text="Статус не проверялся", anchor="w")
        self.hosts_label.pack(fill="x")
        bar = W.button_bar(body)
        W.add_button(bar, "Проверить", self._hosts_check)
        W.add_button(bar, "Обновить автоматически", self._hosts_apply, width=200)
        W.add_button(bar, "Вручную (блокнот)", self._hosts_manual, secondary=True, width=160)
        W.add_button(bar, "Удалить записи", self._hosts_remove, secondary=True)

        # --- инструменты
        body = W.section(self, "Инструменты")
        bar = W.button_bar(body)
        W.add_button(bar, "Автоподбор стратегии", self._auto_pick)
        W.add_button(bar, "Диагностика", self._diagnostics)
        W.add_button(bar, "Удалить службы", self._remove_services, secondary=True)
        bar = W.button_bar(body)
        W.add_button(bar, "Открыть папку zapret", lambda: winutil.open_path(self.app.core.zapret.root),
                     secondary=True, width=180)
        W.add_button(bar, "Лог winws.exe", self._open_winws_log, secondary=True)
        W.add_button(bar, "Показать команду", self._show_command, secondary=True, width=160)
        W.add_button(bar, "Тест в PowerShell", self._run_tests, secondary=True, width=160)

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
        if installed:
            self.not_installed.pack_forget()
        else:
            self.not_installed.pack(fill="x", padx=4, pady=(0, 10), after=self.winfo_children()[0])
        names = zm.strategies()
        self.strategy_box.configure(values=names or ["—"])
        cur = self.app.core.strategy
        self.strategy_box.set(cur if cur in names else (names[0] if names else "—"))

        gf = zm.load_game_filter()
        self.game_mode.set(GAME_LABELS.get(gf.mode, GAME_LABELS["disabled"]))
        W.set_entry(self.game_tcp, gf.tcp)
        W.set_entry(self.game_udp, gf.udp)

        self.ipset_btn.set(zm.ipset_status() if installed else "")

        fakes = zm.fake_files()
        active = zm.active_fakes() if installed else {}
        for kind, box in self.fake_boxes.items():
            box.configure(values=fakes or ["—"])
            box.set(active.get(kind) or "(свой файл)")

        zm.ensure_user_lists()
        names, custom = zm.app_exclusions() if installed else ([], [])
        for name, cb in self.app_checks.items():
            W.set_switch(cb, name in names)
        W.set_entry(self.app_custom, ", ".join(custom))
        W.set_switch(self.learn_sw, self.app.core.settings.get("zapret", "learn_apps"))
        self._update_learn_info()
        for name, box in self.list_boxes.items():
            W.set_text(box, zm.read_user_list(name))

    def on_status(self, zs: ZapretStatus, ts: TgStatus) -> None:
        if zs.service_installed:
            text = f"Служба zapret: {zs.service_state}"
            if zs.service_strategy:
                text += f" · стратегия {zs.service_strategy}"
        else:
            text = "Служба zapret не установлена"
        text += " · winws.exe " + ("запущен" if zs.running else "не запущен")
        self.service_label.configure(text=text)

    # ------------------------------------------------------------------ helpers
    def _after_change(self, what: str) -> None:
        """Перезапуск обхода, если он работает, чтобы изменения вступили в силу."""
        def done(_, err):
            self.app.pages["home"][1].zapret_card.set_busy(False)
            if err:
                self.app.error(str(err), "Zapret")
        st = self.app.zapret_status
        if st.running or st.service_installed:
            self.app.pages["home"][1].zapret_card.set_busy(True, f"Применение: {what}…")
            self.app.run_task(self.app.core.zapret_apply, done)

    # ------------------------------------------------------------------ обработчики
    def _strategy_changed(self, value: str) -> None:
        if value == "—":
            return
        self.app.core.settings.set("zapret", "strategy", value)
        self.app.pages["home"][1].reload_strategies()
        self._after_change("стратегия")

    def _mode_changed(self, label: str) -> None:
        mode = "service" if label == MODE_LABELS["service"] else "process"
        self.app.core.settings.set("zapret", "mode", mode)
        self.app.pages["home"][1].mode_btn.set("Служба Windows" if mode == "service" else "Программа")
        self._after_change("режим")

    def _save_game_filter(self) -> None:
        mode = next((k for k, v in GAME_LABELS.items() if v == self.game_mode.get()), "disabled")
        tcp = validate_ports(self.game_tcp.get())
        udp = validate_ports(self.game_udp.get())
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
            return
        except Exception as exc:
            self.app.error(str(exc))
            self.ipset_btn.set(zm.ipset_status())
            return

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
        names = [n for n, cb in self.app_checks.items() if cb.get()]
        custom = [d for d in self.app_custom.get().replace(";", ",").replace(" ", ",").split(",") if d.strip()]
        zm.set_app_exclusions(names, custom)
        W.set_text(self.list_boxes["list-exclude-user.txt"], zm.read_user_list("list-exclude-user.txt"))
        self.app.core.update_learner()
        self._update_learn_info()
        self.app.info("Исключения применены. Перезапуск обхода не нужен — zapret сам перечитывает списки.")

    def _learn_changed(self) -> None:
        on = bool(self.learn_sw.get())
        self.app.core.settings.set("zapret", "learn_apps", on)
        self.app.core.update_learner()
        self._update_learn_info()

    def _learn_clear(self) -> None:
        self.app.core.learner.clear()
        W.set_text(self.list_boxes["ipset-exclude-user.txt"],
                   self.app.core.zapret.read_user_list("ipset-exclude-user.txt"))
        self._update_learn_info()

    def _update_learn_info(self) -> None:
        count = len(self.app.core.learner.learned())
        text = ("Раз в 8 секунд программа смотрит, к каким адресам подключены отмеченные программы (только "
                "локальная таблица соединений — сеть не нагружается), и добавляет их в ipset-exclude-user.txt. "
                "Первое подключение к новому адресу ещё идёт через обход. Адреса Discord и YouTube "
                f"не исключаются. Выучено адресов: {count}.")
        self.learn_info.configure(text=text)

    def _auto_pick(self) -> None:
        if not self.app.core.zapret.is_installed():
            self.app.error("zapret ещё не загружен")
            return
        from .strategy_dialog import StrategyDialog
        StrategyDialog(self.app)

    def _save_list(self, name: str) -> None:
        zm = self.app.core.zapret
        zm.write_user_list(name, self.list_boxes[name].get("1.0", "end-1c"))
        W.set_text(self.list_boxes[name], zm.read_user_list(name))
        self.app.info(f"{name} сохранён. zapret подхватит изменения сам, без перезапуска.")

    # ------------------------------------------------------------------ hosts
    def _hosts_check(self) -> None:
        def done(res, err):
            if err:
                self.hosts_label.configure(text=f"Ошибка проверки: {err}", text_color=W.RED)
                return
            needs, _ = res
            self.hosts_label.configure(text="hosts нужно обновить" if needs else "hosts актуален",
                                       text_color=W.YELLOW if needs else W.GREEN)
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
                self.hosts_label.configure(text="hosts обновлён", text_color=W.GREEN)
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
        root = self.app.core.zapret.root
        self.app.run_task(lambda: zapret_diag.run_diagnostics(root),
                          lambda res, err: self.app.error(str(err)) if err else DiagnosticsWindow(self.app, res))

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
        zm = self.app.core.zapret
        try:
            cmd = zm.command_line(self.app.core.strategy)
        except Exception as exc:
            self.app.error(str(exc))
            return
        win = ctk.CTkToplevel(self.app)
        win.title("Команда запуска winws.exe")
        win.geometry("760x420")
        box = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        box.pack(fill="both", expand=True, padx=10, pady=10)
        W.set_text(box, cmd.replace(" --new ", " --new" + os.linesep), readonly=True)
        win.after(100, win.lift)


class DiagnosticsWindow(ctk.CTkToplevel):
    COLORS = {zapret_diag.OK: W.GREEN, zapret_diag.WARN: W.YELLOW, zapret_diag.ERROR: W.RED}
    ICONS = {zapret_diag.OK: "✔", zapret_diag.WARN: "?", zapret_diag.ERROR: "✖"}

    def __init__(self, app: "App", checks):
        super().__init__(app)
        self.app = app
        self.title("Диагностика zapret")
        self.geometry("780x600")
        frame = ctk.CTkScrollableFrame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        for c in checks:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=self.ICONS[c.level], width=24, text_color=self.COLORS[c.level],
                         font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", anchor="n")
            ctk.CTkLabel(row, text=c.text, anchor="w", justify="left", wraplength=640,
                         text_color=self.COLORS[c.level] if c.level != zapret_diag.OK else None
                         ).pack(side="left", fill="x", expand=True)
            if c.link:
                ctk.CTkButton(row, text="Подробнее", width=90, height=24,
                              command=lambda u=c.link: winutil.open_url(u)).pack(side="right")
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(0, 10))
        W.add_button(bar, "Удалить конфликтующие службы", self._remove_conflicts, width=240)
        W.add_button(bar, "Очистить кэш Discord", self._clear_discord, width=180)
        W.add_button(bar, "Закрыть", self.destroy, secondary=True)
        self.after(100, self.lift)

    def _remove_conflicts(self) -> None:
        self.app.run_task(zapret_diag.remove_conflicting_services,
                          lambda r, e: self.app.error(str(e)) if e else
                          self.app.info("Удалено: " + (", ".join(r) if r else "конфликтующих служб нет")))

    def _clear_discord(self) -> None:
        if not self.app.ask("Discord будет закрыт, его кэш (Cache, Code Cache, GPUCache) удалён. Продолжить?"):
            return
        self.app.run_task(zapret_diag.clear_discord_cache,
                          lambda r, e: self.app.error(str(e)) if e else self.app.info("\n".join(r)))
