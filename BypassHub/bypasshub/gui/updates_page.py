from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, List

import customtkinter as ctk

from .. import tgproxy, winutil, zapret
from ..core import UpdateInfo
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

INTERVALS = {0: "Только при запуске", 1: "Каждый час", 3: "Каждые 3 часа", 6: "Каждые 6 часов",
             12: "Каждые 12 часов", 24: "Раз в сутки"}
REPOS = {"zapret": zapret.REPO, "tg": tgproxy.REPO}


class UpdatesPage(ctk.CTkScrollableFrame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, fg_color=W.P.window_bg, scrollbar_button_color=W.P.border)
        self.app = app
        self.busy = False
        self.infos: Dict[str, UpdateInfo] = {}
        s = app.core.settings

        W.page_title(self, "Обновления", "Автоматическая загрузка новых версий с GitHub")

        body = W.section(self, "Компоненты",
                         "Программа сама проверяет GitHub-репозитории проектов и скачивает новые релизы. "
                         "При обновлении zapret сохраняются ваши списки, Game Filter, IPSet, фейки и свои "
                         "стратегии; работающий обход перезапускается автоматически.")
        self.rows: Dict[str, Dict[str, ctk.CTkBaseClass]] = {}
        for key, title in (("zapret", "Zapret"), ("tg", "TG WS Proxy")):
            fr = ctk.CTkFrame(body, fg_color=W.P.surface2, corner_radius=12)
            fr.pack(fill="x", pady=4)
            left = ctk.CTkFrame(fr, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=14, pady=10)
            head = ctk.CTkFrame(left, fg_color="transparent")
            head.pack(fill="x")
            ctk.CTkLabel(head, text=title, anchor="w", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
            ver = ctk.CTkLabel(head, text="", anchor="w", text_color=W.P.muted)
            ver.pack(side="left", padx=(12, 0))
            state = ctk.CTkLabel(left, text="", anchor="w", justify="left", wraplength=480)
            state.pack(fill="x")
            ctk.CTkButton(fr, text="GitHub", width=76, height=32, fg_color="transparent", border_width=1,
                          border_color=W.P.border, text_color=W.P.text, hover_color=W.P.border,
                          command=lambda r=REPOS[key]: winutil.open_url(f"https://github.com/{r}/releases")
                          ).pack(side="right", padx=(6, 14))
            btn = ctk.CTkButton(fr, text="Переустановить", width=140, height=32,
                                command=lambda k=key: self.install_one(k))
            btn.pack(side="right")
            self.rows[key] = {"ver": ver, "state": state, "btn": btn}

        self.progress = ctk.CTkProgressBar(body)
        self.progress.set(0)
        self.status = ctk.CTkLabel(body, text="", anchor="w", text_color=W.GRAY)
        self.status.pack(fill="x", pady=(6, 0))
        bar = W.button_bar(body)
        self.check_btn = W.add_button(bar, "Проверить сейчас", lambda: self.app.run_updates(), width=160)
        self.install_btn = W.add_button(bar, "Установить все обновления",
                                        lambda: self.app.run_updates(force_install=True), width=220)

        body = W.section(self, "Параметры")
        self.check_on_start = ctk.CTkSwitch(body, text="Проверять обновления при запуске",
                                            command=lambda: s.set("updates", "check_on_start",
                                                                  bool(self.check_on_start.get())))
        self.check_on_start.pack(anchor="w", pady=3)
        self.auto_install = ctk.CTkSwitch(body, text="Устанавливать обновления автоматически",
                                          command=lambda: s.set("updates", "auto_install",
                                                                bool(self.auto_install.get())))
        self.auto_install.pack(anchor="w", pady=3)
        self.interval = W.row(body, "Периодическая проверка", lambda p: ctk.CTkComboBox(
            p, values=list(INTERVALS.values()), state="readonly", command=self._interval_changed))
        assets = {"auto": "Автоматически"}
        assets.update(tgproxy.ASSETS)
        self._assets = assets
        self.asset = W.row(body, "Сборка TG WS Proxy", lambda p: ctk.CTkComboBox(
            p, values=list(assets.values()), state="readonly", command=self._asset_changed),
            hint="Если антивирус блокирует основную сборку, попробуйте версию для Windows 7 — "
                 "по возможностям она не отличается.")

        W.set_switch(self.check_on_start, s.get("updates", "check_on_start"))
        W.set_switch(self.auto_install, s.get("updates", "auto_install"))
        self.interval.set(INTERVALS.get(int(s.get("updates", "interval_hours")), INTERVALS[6]))
        self.asset.set(assets.get(s.get("tg", "asset"), "Автоматически"))
        self.render()

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.render()

    def _interval_changed(self, label: str) -> None:
        hours = next((h for h, v in INTERVALS.items() if v == label), 6)
        self.app.core.settings.set("updates", "interval_hours", hours)

    def _asset_changed(self, label: str) -> None:
        key = next((k for k, v in self._assets.items() if v == label), "auto")
        self.app.core.settings.set("tg", "asset", key)

    def render(self) -> None:
        core = self.app.core
        installed = {
            "zapret": core.zapret.local_version() if core.zapret.is_installed() else "",
            "tg": core.tg.local_version() if core.tg.is_installed() else "",
        }
        for key, row in self.rows.items():
            info = self.infos.get(key)
            cur = installed[key] or "не установлен"
            latest = info.latest if info and info.latest else "?"
            row["ver"].configure(text=f"установлена: {cur} · последняя: {latest}")
            if info is None:
                row["state"].configure(text="", text_color=W.GRAY)
            elif info.error:
                row["state"].configure(text=f"Не удалось проверить: {info.error}", text_color=W.RED)
            elif info.available:
                row["state"].configure(text="Доступно обновление", text_color=W.YELLOW)
            else:
                row["state"].configure(text="Актуальная версия", text_color=W.GREEN)
            avail = bool(info and info.available)
            row["btn"].configure(text="Обновить" if avail and installed[key] else
                                 ("Установить" if not installed[key] else "Переустановить"),
                                 state="disabled" if self.busy else "normal")
        last = int(core.settings.get("updates", "last_check") or 0)
        if not self.busy:
            self.status.configure(text="Последняя проверка: " +
                                  (time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "ещё не было"))

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        for w in (self.check_btn, self.install_btn):
            w.configure(state="disabled" if busy else "normal")
        if busy:
            self.status.configure(text=text)
            self.progress.pack(fill="x", pady=(6, 0), before=self.status)
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.pack_forget()
        self.render()

    def on_progress(self, component: str, done: int, total: int) -> None:
        name = "Zapret" if component == "zapret" else "TG WS Proxy"
        if total:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(done / total)
            self.status.configure(text=f"Загрузка {name}: {done / 1048576:.1f} из {total / 1048576:.1f} МБ")
        else:
            self.status.configure(text=f"Загрузка {name}: {done / 1048576:.1f} МБ")

    def on_infos(self, infos: List[UpdateInfo]) -> None:
        self.infos = {i.component: i for i in infos}
        self.render()

    def show_error(self, text: str) -> None:
        self.status.configure(text=f"Ошибка: {text}", text_color=W.RED)

    def install_one(self, key: str) -> None:
        if self.busy:
            return
        self.set_busy(True, "Получение информации о релизе…")
        core = self.app.core

        def progress(component, done, total):
            self.app.call_ui(lambda: self.on_progress(component, done, total))

        def work():
            info = self.infos.get(key)
            if info is None or info.release is None:
                info = core.check_zapret() if key == "zapret" else core.check_tg()
            core.install(info, progress)
            return info

        def done(info, err):
            self.set_busy(False)
            if err:
                self.show_error(str(err))
                self.app.error(str(err), "Обновление")
                return
            self.infos[key] = info
            self.render()
            self.app.pages["home"][1].reload_strategies()
            self.app.pages["zapret"][1].reload()
            self.status.configure(text=f"{info.title} {info.latest} установлен", text_color=W.GREEN)
        self.app.run_task(work, done)
