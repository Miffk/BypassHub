"""Главная: три плитки вкл/выкл и список серверов VPN; общая логика переключения сервисов."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout

from .. import netcheck, tgproxy, winutil
from ..core import UpdateInfo, VpnState
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import style as S
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from .app import App


class ServiceCard(W.Card):
    """Шапка страницы сервиса: название, переключатель и подробный статус; кнопки — в self.extra."""

    def __init__(self, title: str, subtitle: str, on_toggle: Callable[[bool], None]):
        super().__init__(margins=(22, 20, 22, 18), spacing=4)
        self.on_toggle = on_toggle
        self.busy = False
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(W.label(title, "title"))
        left.addWidget(W.label(subtitle, "muted"))
        top.addLayout(left, 1)
        self.switch = W.Toggle(big=True)
        self.switch.clicked.connect(self._clicked)
        top.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(top)
        self.status = W.label("…", "status")
        self.body.addSpacing(6)
        self.body.addWidget(self.status)
        self.details = W.label("", "muted", wrap=True)
        self.body.addWidget(self.details)
        self.extra = QHBoxLayout()
        self.extra.setSpacing(8)
        self.extra.setContentsMargins(0, 8, 0, 0)
        self.body.addLayout(self.extra)

    def add_buttons(self, *buttons) -> None:
        for b in buttons:
            self.extra.addWidget(b)
        self.extra.addStretch(1)

    def _clicked(self, checked: bool) -> None:
        if self.busy:
            W.set_checked(self.switch, not checked)
            return
        self.on_toggle(bool(checked))

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.switch.setEnabled(not busy)
        if busy:
            W.set_text(self.status, text)
            W.set_label_color(self.status, S.yellow())

    def set_state(self, on: bool, status: str, color: Optional[str], details: str) -> None:
        if not self.busy:
            W.set_checked(self.switch, on)
            W.set_text(self.status, status)
            W.set_label_color(self.status, color)
        W.set_text(self.details, details)


class ServiceControl:
    """Состояние одного сервиса во всех местах сразу: плитка на главной и шапка страницы сервиса."""

    def __init__(self, tile: W.PowerTile, on_toggle: Callable[[bool], None]):
        self.tile = tile
        self.on_toggle = on_toggle
        self.headers: List[ServiceCard] = []
        self.busy = False
        self._state: Tuple[bool, str, Optional[str], str, str, str] = (False, "…", None, "", "", "…")

    def add_header(self, header: ServiceCard) -> None:
        self.headers.append(header)
        on, status, color, details, _, _ = self._state
        header.set_state(on, status, color, details)

    @property
    def on(self) -> bool:
        return self._state[0]

    def toggle(self) -> None:
        if not self.busy:
            self.on_toggle(not self.on)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        for h in self.headers:
            h.set_busy(busy, text)
        if busy:
            self.tile.set(busy=True, status=text)
        else:
            on, status, color, details, tile_details, tile_status = self._state
            self.tile.set(busy=False, on=on, status=tile_status, details=tile_details)
            for h in self.headers:
                h.set_state(on, status, color, details)

    def set_state(self, on: bool, status: str, color: Optional[str], details: str,
                  tile_details: Optional[str] = None, tile_status: Optional[str] = None) -> None:
        tile_details = details if tile_details is None else tile_details
        tile_status = status if tile_status is None else tile_status
        self._state = (on, status, color, details, tile_details, tile_status)
        for h in self.headers:
            h.set_state(on, status, color, details)
        if self.busy:
            self.tile.set(details=tile_details)
        else:
            self.tile.set(on=on, status=tile_status, details=tile_details)

    def set_switch(self, on: bool) -> None:
        for h in self.headers:
            W.set_checked(h.switch, on)
        self.tile.set(on=on)


class HomePage(Page):
    """Главная: три большие кнопки вкл/выкл. Настройки каждого сервиса — в меню слева."""

    PING_MAX_AGE = 300  # с: при открытии главной/вкладки VPN пинг обновляется, если старше
    CHECK_PERIOD_MS = 180_000

    def __init__(self, app: "App"):
        super().__init__(app)
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(W.label("BypassHub", "title"))
        left.addWidget(W.label("Нажмите на сервис, чтобы включить или выключить его. Настройки — в меню слева.",
                               "muted"))
        head.addLayout(left, 1)
        # заново проверить доступность Discord / YouTube / Telegram
        self.refresh_btn = W.button("⟳  Обновить", self.check_services, width=130)
        head.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(head)

        self.banner = W.Card(name="banner", margins=(14, 10, 10, 10))
        banner_row = QHBoxLayout()
        self.banner_label = W.label("", wrap=True)
        banner_row.addWidget(self.banner_label, 1)
        banner_row.addWidget(W.button("Обновить", lambda: app.run_updates(force_install=True), primary=True))
        self.banner.body.addLayout(banner_row)
        self.banner.hide()
        self.lay.addWidget(self.banner)

        tiles = QGridLayout()
        tiles.setHorizontalSpacing(12)
        self.zapret_card = self._tile(tiles, 0, "Zapret", self.toggle_zapret)
        self.tg_card = self._tile(tiles, 1, "TG WS Proxy", self.toggle_tg)
        self.vpn_card = self._tile(tiles, 2, "VPN", self.toggle_vpn)
        self.lay.addLayout(tiles)

        # серверы VPN: выбор, пинг, «авто» (показывается, когда серверов больше одного)
        from .server_list import ServerPicker
        self.vpn_pings: Dict[str, Optional[float]] = {}
        self.vpn_pings_real = False
        self._ping_at = 0.0
        self._pinging = False
        self.servers_card = W.Card(margins=(18, 14, 18, 14))
        shead = QHBoxLayout()
        shead.addWidget(W.label("Серверы VPN", "h2"))
        self.ping_note = W.label("", "small")
        shead.addWidget(self.ping_note)
        shead.addStretch(1)
        # подписки обновляются и сами, но новые серверы бывают нужны сразу
        self.subs_btn = W.button("⟳ Обновить серверы", lambda: self.app.pages["vpn"][1].refresh_subs())
        self.ping_btn = W.button("Проверить пинг", self.ping_servers, width=150)
        shead.addWidget(self.subs_btn)
        shead.addWidget(self.ping_btn)
        self.servers_card.body.addLayout(shead)
        self.servers = ServerPicker(app, compact=True)
        self.servers_card.body.addWidget(self.servers)
        self.servers_card.hide()
        self.lay.addWidget(self.servers_card)
        self.pickers = [self.servers]
        self.finish()

        self._avail: Dict[str, netcheck.ServiceState] = {}
        self._vpn_state = VpnState()
        self._zs, self._ts = ZapretStatus(), TgStatus()
        self._checking = False
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.timeout.connect(self.check_services)
        self._periodic = QTimer(self)
        self._periodic.timeout.connect(self._periodic_check)
        QTimer.singleShot(1500, self._periodic_check)
        self._periodic.start(self.CHECK_PERIOD_MS)

    def _tile(self, grid: QGridLayout, column: int, title: str, on_toggle: Callable[[bool], None]) -> ServiceControl:
        tile = W.PowerTile(title)
        grid.addWidget(tile, 0, column)
        grid.setColumnStretch(column, 1)
        control = ServiceControl(tile, on_toggle)
        tile.clicked.connect(lambda: control.toggle())
        control.set_state(False, "…", None, "")
        return control

    # ------------------------------------------------------------------ zapret
    def toggle_zapret(self, on: bool) -> None:
        self.zapret_card.set_busy(True, "Включение…" if on else "Выключение…")
        fn = self.app.core.zapret_enable if on else self.app.core.zapret_disable
        self.app.run_task(fn, lambda r, e: self._zapret_done(e), "Zapret")

    def toggle_zapret_from_tray(self) -> None:
        if not self.zapret_card.busy:
            self.toggle_zapret(not self.app.zapret_status.running)

    def _zapret_done(self, error) -> None:
        self.zapret_card.set_busy(False)
        on = self.app.core.settings.get("zapret", "enabled")
        self.app.notify_if_hidden(f"Zapret: ошибка — {error}" if error else
                                  ("Zapret включён" if on else "Zapret выключен"))
        self.check_services(delay_ms=2500)
        if error and self.app.is_shown():
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
        on = self.app.core.settings.get("tg", "enabled")
        self.app.notify_if_hidden(f"TG WS Proxy: ошибка — {error}" if error else
                                  ("TG WS Proxy включён" if on else "TG WS Proxy выключен"))
        self.check_services(delay_ms=2500)
        if error and self.app.is_shown():
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

    # ------------------------------------------------------------------ VPN
    def reload_vpn(self) -> None:
        """Список серверов или выбранный сервер изменились."""
        self._render_vpn()

    def apply_vpn(self, busy_text: str) -> None:
        """Перезапустить VPN с новыми настройками, если он включён."""
        if not self._vpn_state.running or self.vpn_card.busy:
            return
        self.vpn_card.set_busy(True, busy_text)
        self.app.run_task(self.app.core.vpn_apply, lambda r, e: self._vpn_done(e), "VPN")

    def toggle_vpn(self, on: bool) -> None:
        core = self.app.core
        if on and not core.vpn_profiles():
            self.vpn_card.set_switch(False)
            self.app.show_page("vpn")
            self.app.info("Сначала добавьте свой сервер: вставьте ключ vless://… или ключ AmneziaVPN vpn://…",
                          "VPN")
            return
        self.vpn_card.set_busy(True, "Подключение…" if on else "Отключение…")

        def progress(component: str, done: int, total: int) -> None:
            text = f"Загрузка: {done / 1048576:.1f}" + (f" из {total / 1048576:.1f}" if total else "") + " МБ"
            self.app.call_ui(lambda: self.vpn_card.set_busy(True, text))
        fn = (lambda: core.vpn_enable(progress)) if on else core.vpn_disable
        self.app.run_task(fn, lambda r, e: self._vpn_done(e), "VPN")

    def toggle_vpn_from_tray(self) -> None:
        if not self.vpn_card.busy:
            self.toggle_vpn(not self._vpn_state.running)

    def _vpn_done(self, error) -> None:
        self.vpn_card.set_busy(False)
        on = self.app.core.settings.get("vpn", "enabled")
        self.app.notify_if_hidden(f"VPN: ошибка — {error}" if error else ("VPN включён" if on else "VPN выключен"))
        self.check_services(delay_ms=3000)
        self.app.pages["vpn"][1].render()
        if error and self.app.is_shown():
            self.app.error(str(error), "VPN")

    def on_vpn_status(self, vs: VpnState) -> None:
        self._vpn_state = vs
        if vs.running and vs.kind == "vless" and vs.delays:
            # задержки из проверок самого sing-box (в режиме «авто» — раз в минуту)
            self.vpn_pings.update({k: float(v) for k, v in vs.delays.items()})
            self.vpn_pings_real = True
        self._render_vpn()

    def _server_name(self, pid: str) -> str:
        return next((p.name for p in self.app.core.vpn_profiles() if p.id == pid), pid)

    def _render_vpn(self) -> None:
        vs = self._vpn_state
        core = self.app.core
        active = core.vpn_active()
        auto = core.vpn_is_auto()
        profiles = core.vpn_profiles()
        self.servers_card.setVisible(len(profiles) >= 2)
        for picker in self.pickers:
            picker.render()
        self.subs_btn.setVisible(bool(core.settings.get("vpn", "subscriptions")))
        if vs.running:
            how = "весь трафик" if vs.mode == "tun" else "системный прокси"
            proto = "AmneziaWG" if vs.kind == "awg" else "VLESS"
            details = f"{proto} · {how}"
            if vs.singbox and vs.singbox.running and core.settings.get("vpn", "ru_direct"):
                details += " · российское — напрямую"
            if vs.kind == "vless" and vs.current:
                ms = self.vpn_pings.get(vs.current)
                name_ping = self._server_name(vs.current) + (f" · {ms:.0f} мс" if ms else "")
                title = ("Авто: " if vs.selected == "auto" else "") + name_ping
            else:
                title = active.name if active else "—"
            self.vpn_card.set_state(True, f"● Подключён: {title}", S.green(), details,
                                    tile_details=f"{title}\n{details}", tile_status="● Подключён")
        elif auto:
            details = "" if core.singbox.is_installed() else "При первом включении скачается движок VPN с GitHub."
            self.vpn_card.set_state(False, "○ Выключен", None, details, tile_details="Авто — лучший сервер")
        elif active is None:
            self.vpn_card.set_state(False, "○ Нет серверов", None, "Добавьте ключ VLESS или ключ AmneziaVPN ниже.",
                                    tile_details="Добавьте свой ключ во вкладке VPN")
        else:
            engine = core.awg if active.kind == "awg" else core.singbox
            details = "" if engine.is_installed() else "При первом включении скачается движок VPN с GitHub."
            self.vpn_card.set_state(False, "○ Выключен", None, details, tile_details=f"Сервер: {active.name}")

    def select_server(self, pid: str) -> None:
        """Выбор сервера (или «auto») на главной или во вкладке VPN."""
        core = self.app.core
        if core.settings.get("vpn", "active") == pid:
            return
        core.vpn_select(pid)
        self._render_vpn()
        self.app.pages["vpn"][1].render()
        if self._vpn_state.running and not self.vpn_card.busy:
            self.vpn_card.set_busy(True, "Переключение…")
            self.app.run_task(core.vpn_switch, lambda r, e: self._vpn_done(e), "VPN")

    def ping_servers(self, max_age: float = 0) -> None:
        core = self.app.core
        if self._pinging or not core.vpn_vless() or time.time() - self._ping_at < max_age:
            return
        self._pinging = True
        for btn in self._ping_buttons():
            btn.setText("Проверка…")
            btn.setEnabled(False)

        def done(result, err):
            self._pinging = False
            self._ping_at = time.time()
            for btn in self._ping_buttons():
                btn.setText("Проверить пинг")
                btn.setEnabled(True)
            if not err and result:
                self.vpn_pings, self.vpn_pings_real = dict(result[0]), result[1]
            self.ping_note.setText(("через сервер" if self.vpn_pings_real else "до сервера") if self.vpn_pings else "")
            self._render_vpn()
        self.app.run_task(core.vpn_ping, done)

    def _ping_buttons(self) -> list:
        btns = [self.ping_btn]
        vp = self.app.pages.get("vpn")
        if vp and hasattr(vp[1], "ping_btn"):
            btns.append(vp[1].ping_btn)
        return btns

    def on_show(self) -> None:
        self.ping_servers(max_age=self.PING_MAX_AGE)

    def on_palette(self) -> None:
        self.servers.render(force=True)

    # ------------------------------------------------------------------ доступность сервисов
    def _periodic_check(self) -> None:
        if self.app.is_shown():  # не тратим трафик, пока окно скрыто в трее
            self.check_services()

    def check_services(self, delay_ms: int = 0) -> None:
        if delay_ms:
            self._check_timer.start(delay_ms)
            return
        if self._checking:
            return
        self._checking = True
        self.refresh_btn.setText("Проверка…")
        self.refresh_btn.setEnabled(False)
        self.on_status(self._zs, self._ts)  # на плитках — «проверка…»
        self.app.run_task(netcheck.check_services, self._services_done)

    def _services_done(self, states, error) -> None:
        self._checking = False
        self.refresh_btn.setText("⟳  Обновить")
        self.refresh_btn.setEnabled(True)
        if not error and states:
            self._avail = {st.name: st for st in states}
        self.on_status(self._zs, self._ts)

    def availability(self, names) -> str:
        """«Discord: доступен · YouTube: недоступен» по последней проверке."""
        if self._checking:
            return "Проверка доступности…"
        parts = []
        for name in names:
            st = self._avail.get(name)
            if st is None:
                continue
            parts.append(f"{name}: " + ("доступен" if st.ok else ("частично" if st.partial else "недоступен")))
        return " · ".join(parts)

    # ------------------------------------------------------------------ статус
    def on_status(self, zs: ZapretStatus, ts: TgStatus) -> None:
        self._zs, self._ts = zs, ts
        core = self.app.core
        avail_z = self.availability(("Discord", "YouTube"))
        avail_t = self.availability(("Telegram",))

        def with_avail(text: str, avail: str) -> str:
            return "\n".join(x for x in (text, avail) if x)

        mode = "служба Windows" if zs.service_installed else "программа"
        if not zs.installed:
            self.zapret_card.set_state(False, "Не установлен", None, "Загрузка начнётся автоматически.",
                                       tile_details="Скачивается…")
        elif zs.running:
            strategy = (zs.service_strategy + ".bat") if zs.service_strategy else core.strategy
            details = f"Стратегия: {strategy} · режим: {mode} · версия {zs.version}"
            if zs.foreign_processes:
                details += "\nВнимание: запущен winws.exe из другой папки: " + ", ".join(zs.foreign_processes)
            self.zapret_card.set_state(True, "● Работает", S.green(), with_avail(details, avail_z),
                                       tile_details=with_avail(strategy.removesuffix(".bat"), avail_z))
        elif zs.service_installed:
            self.zapret_card.set_state(True, "⚠ Служба не работает", S.yellow(),
                                       f"Служба установлена, но не работает ({zs.service_state}). "
                                       "Попробуйте другую стратегию или запустите диагностику.",
                                       tile_details="Выберите другую стратегию во вкладке Zapret")
        else:
            self.zapret_card.set_state(False, "○ Выключен", None, with_avail(f"Версия {zs.version}", avail_z),
                                       tile_details=avail_z)

        if not ts.installed:
            self.tg_card.set_state(False, "Не установлен", None, "Загрузка начнётся автоматически.",
                                   tile_details="Скачивается…")
        elif ts.running:
            cfg = core.tg.load_config()
            addr = f"{cfg.get('host')}:{cfg.get('port')}"
            if ts.listening:
                self.tg_card.set_state(True, f"● Работает на {addr}", S.green(),
                                       with_avail(f"Версия {ts.version}", avail_t),
                                       tile_details=with_avail(addr, avail_t), tile_status="● Работает")
            else:
                self.tg_card.set_state(True, "⚠ Порт не отвечает", S.yellow(),
                                       "Процесс запущен, но порт не отвечает. Проверьте лог прокси ниже.",
                                       tile_details="Проверьте лог прокси во вкладке TG WS Proxy")
        else:
            details = f"Версия {ts.version}"
            if ts.foreign:
                details += "\nЗапущена другая копия TgWsProxy — закройте её перед включением: " + \
                           ", ".join(ts.foreign)
            self.tg_card.set_state(False, "○ Выключен", None, with_avail(details, avail_t), tile_details=avail_t)

    def on_infos(self, infos: List[UpdateInfo]) -> None:
        pending = [i for i in infos if i.available and i.installed]
        if pending:
            self.banner_label.setText("Доступны обновления: " +
                                      ", ".join(f"{i.title} {i.installed} → {i.latest}" for i in pending))
        self.banner.setVisible(bool(pending))
