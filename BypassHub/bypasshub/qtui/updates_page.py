"""Обновления: версии компонентов, проверка и установка с GitHub, параметры обновления."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, List

from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout

from .. import __version__, awg, selfupdate, tgproxy, vpn, winutil, zapret
from ..core import UpdateInfo
from . import style as S
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from .app import App

INTERVALS = {0: "Только при запуске", 1: "Каждый час", 3: "Каждые 3 часа", 6: "Каждые 6 часов",
             12: "Каждые 12 часов", 24: "Раз в сутки"}
REPOS = {"zapret": zapret.REPO, "tg": tgproxy.REPO, "singbox": vpn.REPO, "awg": awg.REPO, "self": selfupdate.REPO}
TITLES = {"zapret": "Zapret", "tg": "TG WS Proxy", "singbox": "VPN: sing-box (VLESS)",
          "awg": "VPN: AmneziaWG", "self": "BypassHub"}
# движки VPN не скачиваются заранее — только при первом включении VPN с таким ключом
LAZY = {"singbox", "awg"}


class UpdatesPage(Page):
    def __init__(self, app: "App"):
        super().__init__(app)
        self.busy = False
        self.infos: Dict[str, UpdateInfo] = {}
        s = app.core.settings
        W.page_title(self.lay, "Обновления", "Автоматическая загрузка новых версий с GitHub")

        body = W.section(self.lay, "Компоненты",
                         "Программа сама проверяет GitHub-репозитории проектов и скачивает новые релизы. "
                         "При обновлении zapret сохраняются ваши списки, Game Filter, IPSet, фейки и свои "
                         "стратегии; работающий обход перезапускается автоматически.")
        self.rows: Dict[str, dict] = {}
        for key, title in TITLES.items():
            fr = W.Card(name="row", margins=(14, 10, 12, 10))
            line = QHBoxLayout()
            left = QVBoxLayout()
            left.setSpacing(2)
            ver = W.label("", "muted")
            left.addLayout(W.hbox(W.label(title, "h2"), ver, spacing=12))
            state = W.label("", wrap=True)
            left.addWidget(state)
            line.addLayout(left, 1)
            btn = W.button("Переустановить", lambda k=key: self.install_one(k), primary=True, width=140)
            line.addWidget(btn)
            line.addWidget(W.button("GitHub", lambda r=REPOS[key]: winutil.open_url(f"https://github.com/{r}/releases")))
            fr.body.addLayout(line)
            body.addWidget(fr)
            self.rows[key] = {"ver": ver, "state": state, "btn": btn}

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        body.addWidget(self.progress)
        self.status = W.label("", "muted", wrap=True)
        body.addWidget(self.status)
        self.check_btn = W.button("Проверить сейчас", lambda: self.app.run_updates(), primary=True, width=160)
        self.install_btn = W.button("Установить все обновления", lambda: self.app.run_updates(force_install=True),
                                    primary=True, width=220)
        body.addLayout(W.button_bar(self.check_btn, self.install_btn))

        body = W.section(self.lay, "Параметры")
        self.check_on_start = W.Toggle("Проверять обновления при запуске",
                                       lambda on: s.set("updates", "check_on_start", on))
        self.auto_install = W.Toggle("Устанавливать обновления автоматически",
                                     lambda on: s.set("updates", "auto_install", on))
        self.self_update = W.Toggle("Обновлять сам BypassHub", lambda on: s.set("updates", "self_update", on))
        for t in (self.check_on_start, self.auto_install, self.self_update):
            body.addWidget(t)
        self.interval = W.row(body, "Периодическая проверка", W.combo(INTERVALS.values(), self._interval_changed))
        assets = {"auto": "Автоматически"}
        assets.update(tgproxy.ASSETS)
        self._assets = assets
        self.asset = W.row(body, "Сборка TG WS Proxy", W.combo(assets.values(), self._asset_changed),
                           hint="Если антивирус блокирует основную сборку, попробуйте версию для Windows 7 — "
                                "по возможностям она не отличается.")
        self.finish()

        W.set_checked(self.check_on_start, s.get("updates", "check_on_start"))
        W.set_checked(self.auto_install, s.get("updates", "auto_install"))
        W.set_checked(self.self_update, s.get("updates", "self_update"))
        W.set_combo(self.interval, list(INTERVALS.values()),
                    INTERVALS.get(int(s.get("updates", "interval_hours")), INTERVALS[6]))
        W.set_combo(self.asset, list(assets.values()), assets.get(s.get("tg", "asset"), "Автоматически"))
        self.render()

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.render()

    def _interval_changed(self, text: str) -> None:
        self.app.core.settings.set("updates", "interval_hours", next((h for h, v in INTERVALS.items() if v == text), 6))

    def _asset_changed(self, text: str) -> None:
        self.app.core.settings.set("tg", "asset", next((k for k, v in self._assets.items() if v == text), "auto"))

    def render(self) -> None:
        core = self.app.core
        installed = {
            "zapret": core.zapret.local_version() if core.zapret.is_installed() else "",
            "tg": core.tg.local_version() if core.tg.is_installed() else "",
            "singbox": core.singbox.local_version() if core.singbox.is_installed() else "",
            "awg": core.awg.local_version() if core.awg.is_installed() else "",
            "self": __version__,
        }
        for key, row in self.rows.items():
            info = self.infos.get(key)
            latest = info.latest if info and info.latest else "?"
            W.set_text(row["ver"], f"установлена: {installed[key] or 'не установлен'} · последняя: {latest}")
            if info is None and key in LAZY and not installed[key]:
                text, color = "Скачается при первом включении VPN", S.P.muted
            elif info is None:
                text, color = "", S.P.muted
            elif info.error:
                text, color = f"Не удалось проверить: {info.error}", S.red()
            elif info.available:
                text, color = "Доступно обновление", S.yellow()
            else:
                text, color = "Актуальная версия", S.green()
            W.set_text(row["state"], text)
            W.set_label_color(row["state"], color)
            avail = bool(info and info.available)
            btn = row["btn"]
            if key == "self":
                btn.setText("Обновить")
                btn.setEnabled(avail and not self.busy and selfupdate.is_frozen())
                continue
            btn.setText("Обновить" if avail and installed[key] else ("Установить" if not installed[key]
                                                                      else "Переустановить"))
            btn.setEnabled(not self.busy)
        last = int(core.settings.get("updates", "last_check") or 0)
        if not self.busy:
            W.set_label_color(self.status, None)
            self.status.setText("Последняя проверка: " +
                                (time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "ещё не было"))

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        for w in (self.check_btn, self.install_btn):
            w.setEnabled(not busy)
        if busy:
            W.set_label_color(self.status, None)
            self.status.setText(text)
            self.progress.setRange(0, 0)  # «бегущая» полоса, пока размер загрузки неизвестен
            self.progress.show()
        else:
            self.progress.hide()
        self.render()

    def on_progress(self, component: str, done: int, total: int) -> None:
        name = TITLES.get(component, component)
        if total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done / total * 1000))
            self.status.setText(f"Загрузка {name}: {done / 1048576:.1f} из {total / 1048576:.1f} МБ")
        else:
            self.status.setText(f"Загрузка {name}: {done / 1048576:.1f} МБ")

    def on_infos(self, infos: List[UpdateInfo]) -> None:
        self.infos = {i.component: i for i in infos}
        self.render()

    def show_error(self, text: str) -> None:
        self.status.setText(f"Ошибка: {text}")
        W.set_label_color(self.status, S.red())

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
                info = {"zapret": core.check_zapret, "tg": core.check_tg, "singbox": core.check_singbox,
                        "awg": core.check_awg, "self": core.check_self}[key]()
            core.install(info, progress)
            return info

        def done(info, err):
            self.set_busy(False)
            if err:
                self.show_error(str(err))
                self.app.error(str(err), "Обновление")
                return
            self.infos[key] = info
            if core.restarting_for_update:
                self.app.quit_app()
                return
            self.render()
            self.app.pages["zapret"][1].reload()
            self.status.setText(f"{info.title} {info.latest} установлен")
            W.set_label_color(self.status, S.green())
        self.app.run_task(work, done)
