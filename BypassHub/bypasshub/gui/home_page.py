from __future__ import annotations

from typing import TYPE_CHECKING, List

import customtkinter as ctk

from .. import netcheck, tgproxy, winutil
from ..core import UpdateInfo
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import widgets as W

if TYPE_CHECKING:
    from .app import App


class ServiceCard(ctk.CTkFrame):
    def __init__(self, parent, title: str, subtitle: str, on_toggle):
        super().__init__(parent, fg_color=W.P.surface, corner_radius=18)
        self.on_toggle = on_toggle
        self.busy = False

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=22, pady=(20, 4))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text=title, font=W.title_font(20), anchor="w").pack(fill="x")
        ctk.CTkLabel(left, text=subtitle, text_color=W.GRAY, anchor="w").pack(fill="x")

        self.switch = W.GradientSwitch(top, command=self._clicked)
        self.switch.pack(side="right")

        self.status = ctk.CTkLabel(self, text="…", anchor="w", font=ctk.CTkFont(size=14, weight="bold"))
        self.status.pack(fill="x", padx=22, pady=(6, 0))
        self.details = ctk.CTkLabel(self, text="", anchor="w", justify="left", text_color=W.GRAY,
                                    wraplength=640)
        self.details.pack(fill="x", padx=22, pady=(2, 8))
        self.extra = ctk.CTkFrame(self, fg_color="transparent")
        self.extra.pack(fill="x", padx=22, pady=(0, 20))

    def _clicked(self) -> None:
        if self.busy:
            return
        self.on_toggle(bool(self.switch.get()))

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.switch.configure(state="disabled" if busy else "normal")
        if busy:
            self.status.configure(text=text, text_color=W.YELLOW)

    def set_state(self, on: bool, status: str, color, details: str) -> None:
        if not self.busy:
            W.set_switch(self.switch, on)
            self.status.configure(text=status, text_color=color)
        self.details.configure(text=details)


class HomePage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, fg_color=W.P.window_bg, scrollbar_button_color=W.P.border)
        self.app = app
        core = app.core

        self.hero = W.Hero(self)
        self.hero.pack(fill="x", padx=4, pady=(0, 14))
        self.hero.set_text("BypassHub", "Проверяю состояние…")

        # индикаторы доступности сервисов
        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(fill="x", padx=4, pady=(0, 14))
        self.chips = {}
        for i, name in enumerate(netcheck.SERVICES):
            chip = ctk.CTkFrame(chips, fg_color=W.P.surface, corner_radius=14)
            chip.grid(row=0, column=i, sticky="ew", padx=(0, 10))
            chips.grid_columnconfigure(i, weight=1)
            dot = ctk.CTkLabel(chip, text="●", text_color=W.P.muted, font=ctk.CTkFont(size=16), width=20)
            dot.pack(side="left", padx=(14, 4), pady=10)
            ctk.CTkLabel(chip, text=name, font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
            info = ctk.CTkLabel(chip, text="…", text_color=W.P.muted)
            info.pack(side="right", padx=(4, 14))
            self.chips[name] = (dot, info, chip)
        refresh = ctk.CTkButton(chips, text="⟳", width=44, height=44, corner_radius=14, fg_color=W.P.surface,
                                hover_color=W.P.surface2, text_color=W.P.text, font=ctk.CTkFont(size=18),
                                command=self.check_services)
        refresh.grid(row=0, column=len(self.chips), sticky="e")
        self._checking = False
        self._check_job = None
        self.after(1500, self._periodic_check)

        self.banner = ctk.CTkFrame(self, fg_color=W.P.surface, corner_radius=14, border_width=1,
                                   border_color=W.P.accent)
        self.banner_label = ctk.CTkLabel(self.banner, text="", anchor="w", justify="left")
        self.banner_label.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        W.GradientButton(self.banner, text="Обновить", width=110,
                         command=lambda: app.run_updates(force_install=True)).pack(side="right", padx=10, pady=8)

        self.zapret_card = ServiceCard(self, "Zapret", "Обход блокировок Discord, YouTube и др. (winws.exe + WinDivert)",
                                       self.toggle_zapret)
        self.zapret_card.pack(fill="x", padx=4, pady=(0, 14))
        bar = self.zapret_card.extra
        ctk.CTkLabel(bar, text="Стратегия:").pack(side="left", padx=(0, 6))
        self.strategy_box = ctk.CTkComboBox(bar, values=[], width=300, state="readonly",
                                            command=self._strategy_changed)
        self.strategy_box.pack(side="left")
        self.mode_btn = ctk.CTkSegmentedButton(bar, values=["Программа", "Служба Windows"],
                                               command=self._mode_changed)
        self.mode_btn.pack(side="left", padx=10)
        W.add_button(bar, "Подобрать", lambda: app.pages["zapret"][1]._auto_pick(), secondary=True, width=110)
        self.mode_btn.set("Служба Windows" if core.zapret_mode == "service" else "Программа")

        self.tg_card = ServiceCard(self, "TG WS Proxy", "Локальный MTProto-прокси, ускоряющий Telegram Desktop",
                                   self.toggle_tg)
        self.tg_card.pack(fill="x", padx=4, pady=(0, 14))
        bar = self.tg_card.extra
        W.add_button(bar, "Открыть в Telegram", self.open_in_telegram, width=170)
        W.add_button(bar, "Скопировать ссылку", self.copy_link, secondary=True, width=170)

        hint = ("Подсказка: перебирайте стратегии, пока Discord/YouTube не заработают. Подобрать стратегию "
                "автоматически кнопкой «Подобрать». Для автозапуска при включении ПК "
                "включите режим «Служба Windows» или автозапуск BypassHub в настройках.")
        ctk.CTkLabel(self, text=hint, text_color=W.GRAY, wraplength=680, justify="left",
                     anchor="w").pack(fill="x", padx=8, pady=(4, 0))
        self.reload_strategies()

    # ------------------------------------------------------------------ zapret
    def reload_strategies(self) -> None:
        names = self.app.core.zapret.strategies()
        self.strategy_box.configure(values=names or ["—"])
        current = self.app.core.strategy
        self.strategy_box.set(current if current in names else (names[0] if names else "—"))

    def _strategy_changed(self, value: str) -> None:
        if value == "—":
            return
        core = self.app.core
        core.settings.set("zapret", "strategy", value)
        self._apply_zapret("Смена стратегии…")

    def _mode_changed(self, value: str) -> None:
        mode = "service" if value == "Служба Windows" else "process"
        self.app.core.settings.set("zapret", "mode", mode)
        zp = self.app.pages.get("zapret")
        if zp:
            zp[1].sync_from_settings()
        self._apply_zapret("Смена режима…")

    def _apply_zapret(self, busy_text: str) -> None:
        st = self.app.zapret_status
        if not (st.running or st.service_installed):
            return
        self.zapret_card.set_busy(True, busy_text)
        self.app.run_task(self.app.core.zapret_apply,
                          lambda r, e: self._zapret_done(e), "Zapret")

    def toggle_zapret(self, on: bool) -> None:
        self.zapret_card.set_busy(True, "Включение…" if on else "Выключение…")
        fn = self.app.core.zapret_enable if on else self.app.core.zapret_disable
        self.app.run_task(fn, lambda r, e: self._zapret_done(e), "Zapret")

    def toggle_zapret_from_tray(self) -> None:
        if not self.zapret_card.busy:
            self.toggle_zapret(not self.app.zapret_status.running)

    def _zapret_done(self, error) -> None:
        self.zapret_card.set_busy(False)
        self.check_services(delay_ms=2500)
        if error:
            self.app.error(str(error), "Zapret")

    # ------------------------------------------------------------------ tg
    def toggle_tg(self, on: bool) -> None:
        self.tg_card.set_busy(True, "Запуск…" if on else "Остановка…")
        fn = self.app.core.tg_enable if on else self.app.core.tg_disable
        self.app.run_task(fn, lambda r, e: self._tg_done(e), "TG WS Proxy")

    def toggle_tg_from_tray(self) -> None:
        if not self.tg_card.busy:
            self.toggle_tg(not self.app.tg_status.running)

    def _tg_done(self, error) -> None:
        self.tg_card.set_busy(False)
        self.check_services(delay_ms=2500)
        if error:
            self.app.error(str(error), "TG WS Proxy")

    def open_in_telegram(self) -> None:
        url = tgproxy.proxy_link(self.app.core.tg.load_config())
        if not winutil.open_url(url):
            self.app.copy_to_clipboard(url)
            self.app.info("Не удалось открыть Telegram. Ссылка скопирована — отправьте её себе в "
                          "«Избранное» и нажмите на неё.\n\n" + url)

    def copy_link(self) -> None:
        url = tgproxy.proxy_link(self.app.core.tg.load_config())
        self.app.copy_to_clipboard(url)
        self.app.info("Ссылка скопирована:\n" + url)

    # ------------------------------------------------------------------ доступность сервисов
    CHECK_PERIOD_MS = 180_000

    def _periodic_check(self) -> None:
        if self.app.winfo_viewable():  # не тратим трафик, пока окно скрыто в трее
            self.check_services()
        self.after(self.CHECK_PERIOD_MS, self._periodic_check)

    def check_services(self, delay_ms: int = 0) -> None:
        if delay_ms:
            if self._check_job:
                self.after_cancel(self._check_job)
            self._check_job = self.after(delay_ms, self.check_services)
            return
        self._check_job = None
        if self._checking:
            return
        self._checking = True
        for _dot, info, _ in self.chips.values():
            info.configure(text="проверка…")
        self.app.run_task(netcheck.check_services, self._services_done)

    def _services_done(self, states, error) -> None:
        self._checking = False
        if error or not states:
            return
        for st in states:
            if st.name not in self.chips:
                continue
            dot, info, _ = self.chips[st.name]
            if st.ok:
                dot.configure(text_color=W.GREEN)
                info.configure(text=f"доступен · {st.ms:.0f} мс")
            elif st.partial:
                dot.configure(text_color=W.YELLOW)
                info.configure(text="частично")
            else:
                dot.configure(text_color=W.RED)
                info.configure(text="недоступен")

    # ------------------------------------------------------------------ статус
    def on_status(self, zs: ZapretStatus, ts: TgStatus) -> None:
        core = self.app.core
        mode = "служба Windows" if zs.service_installed else "программа"
        if not zs.installed:
            self.zapret_card.set_state(False, "Не установлен — загрузка начнётся автоматически", W.GRAY, "")
        elif zs.running:
            strategy = (zs.service_strategy + ".bat") if zs.service_strategy else core.strategy
            details = f"Стратегия: {strategy} · режим: {mode} · версия {zs.version}"
            if zs.foreign_processes:
                details += "\nВнимание: запущен winws.exe из другой папки: " + ", ".join(zs.foreign_processes)
            self.zapret_card.set_state(True, "● Работает", W.GREEN, details)
        elif zs.service_installed:
            self.zapret_card.set_state(True, f"⚠ Служба установлена, но не работает ({zs.service_state})",
                                       W.YELLOW, "Попробуйте другую стратегию или запустите диагностику.")
        else:
            self.zapret_card.set_state(False, "○ Выключен", W.GRAY, f"Версия {zs.version}")

        cfg_port = None
        if not ts.installed:
            self.tg_card.set_state(False, "Не установлен — загрузка начнётся автоматически", W.GRAY, "")
        elif ts.running:
            cfg = core.tg.load_config()
            cfg_port = f"{cfg.get('host')}:{cfg.get('port')}"
            if ts.listening:
                self.tg_card.set_state(True, f"● Работает на {cfg_port}", W.GREEN, f"Версия {ts.version}")
            else:
                self.tg_card.set_state(True, "⚠ Процесс запущен, но порт не отвечает", W.YELLOW,
                                       "Проверьте лог прокси во вкладке TG WS Proxy.")
        else:
            details = f"Версия {ts.version}"
            if ts.foreign:
                details += "\nЗапущена другая копия TgWsProxy — закройте её перед включением: " + \
                           ", ".join(ts.foreign)
            self.tg_card.set_state(False, "○ Выключен", W.GRAY, details)
        self._update_hero(zs, ts)

    def _update_hero(self, zs: ZapretStatus, ts: TgStatus) -> None:
        on = [name for name, flag in (("Zapret", zs.running), ("TG Proxy", ts.running)) if flag]
        if len(on) == 2:
            title = "Всё включено"
        elif on:
            title = f"Работает {on[0]}"
        else:
            title = "Всё выключено"
        parts = [f"Zapret: {'вкл' if zs.running else 'выкл'}", f"TG Proxy: {'вкл' if ts.running else 'выкл'}"]
        self.hero.set_text(title, "  ·  ".join(parts))

    def on_infos(self, infos: List[UpdateInfo]) -> None:
        pending = [i for i in infos if i.available and i.installed]
        if pending:
            text = "Доступны обновления: " + ", ".join(f"{i.title} {i.installed} → {i.latest}" for i in pending)
            self.banner_label.configure(text=text)
            self.banner.pack(fill="x", padx=4, pady=(0, 14), before=self.zapret_card)
        else:
            self.banner.pack_forget()
        self.reload_strategies()
