from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from .. import netcheck, tgproxy, winutil
from ..core import UpdateInfo, VpnState
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import widgets as W
from .surface import GradientPage, mark_card

if TYPE_CHECKING:
    from .app import App


class ServiceCard(ctk.CTkFrame):
    """Шапка страницы сервиса: название, переключатель и подробный статус."""

    def __init__(self, parent, title: str, subtitle: str, on_toggle):
        super().__init__(parent, fg_color=W.P.surface, corner_radius=18)
        mark_card(self)
        self.on_toggle = on_toggle
        self.busy = False

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=22, pady=(20, 4))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text=title, font=W.title_font(24), anchor="w").pack(fill="x")
        ctk.CTkLabel(left, text=subtitle, text_color=W.GRAY, anchor="w").pack(fill="x")

        self.switch = W.GradientSwitch(top, command=self._clicked)
        self.switch.pack(side="right")

        self.status = ctk.CTkLabel(self, text="…", anchor="w", font=ctk.CTkFont(size=14, weight="bold"))
        self.status.pack(fill="x", padx=22, pady=(6, 0))
        self.details = ctk.CTkLabel(self, text="", anchor="w", justify="left", text_color=W.GRAY,
                                    wraplength=640)
        self.details.pack(fill="x", padx=22, pady=(2, 8))
        self.extra = ctk.CTkFrame(self, fg_color="transparent", height=1)
        self.extra.pack(fill="x", padx=22, pady=(0, 18))

    def _clicked(self) -> None:
        if self.busy:
            return
        self.on_toggle(bool(self.switch.get()))

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.switch.configure(state="disabled" if busy else "normal")
        if busy:
            W.update(self.status, text=text, text_color=W.YELLOW)

    def set_state(self, on: bool, status: str, color, details: str) -> None:
        if not self.busy:
            W.set_switch(self.switch, on)
            W.update(self.status, text=status, text_color=color)
        W.update(self.details, text=details)


class ServiceControl:
    """Состояние одного сервиса во всех местах сразу: плитка на главной
    и переключатель вверху страницы сервиса."""

    def __init__(self, tile: W.PowerTile, on_toggle: Callable[[bool], None]):
        self.tile = tile
        self.on_toggle = on_toggle
        self.headers: List[ServiceCard] = []
        self.busy = False
        self._state: Tuple[bool, str, object, str, str, str] = (False, "…", W.GRAY, "", "", "…")

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
            on, _, _, _, tile_details, tile_status = self._state
            self.tile.set(busy=False, on=on, status=tile_status, details=tile_details)

    def set_state(self, on: bool, status: str, color, details: str, tile_details: Optional[str] = None,
                  tile_status: Optional[str] = None) -> None:
        """status/details — для шапки страницы; на плитке места меньше: tile_status/tile_details."""
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
            W.set_switch(h.switch, on)
        self.tile.set(on=on)


class HomePage(GradientPage):
    """Главная: три большие кнопки вкл/выкл. Настройки каждого сервиса — в меню слева."""

    def __init__(self, parent, app: "App"):
        super().__init__(parent)
        self.app = app

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=W.PAGE_PADX, pady=(6, 12))
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text="BypassHub", font=W.title_font(26), anchor="w").pack(fill="x", padx=(2, 0))
        ctk.CTkLabel(left, text="Нажмите на сервис, чтобы включить или выключить его. Настройки — в меню слева.",
                     anchor="w", text_color=W.GRAY).pack(fill="x", padx=(2, 0))
        # заново проверить доступность Discord / YouTube / Telegram
        self.refresh_btn = W.add_button(head, "⟳  Обновить", self.check_services, secondary=True, width=130)
        self.refresh_btn.pack_configure(side="right", padx=0)

        self.banner = mark_card(ctk.CTkFrame(self, fg_color=W.P.surface, corner_radius=14, border_width=1,
                                             border_color=W.P.accent))
        self.banner_label = ctk.CTkLabel(self.banner, text="", anchor="w", justify="left")
        self.banner_label.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        W.GradientButton(self.banner, text="Обновить", width=110,
                         command=lambda: app.run_updates(force_install=True)).pack(side="right", padx=10, pady=8)

        self.tiles = ctk.CTkFrame(self, fg_color="transparent")
        self.tiles.pack(fill="x", padx=W.PAGE_PADX, pady=(0, 14))
        self.zapret_card = self._tile(0, "Zapret", self.toggle_zapret)
        self.tg_card = self._tile(1, "TG WS Proxy", self.toggle_tg)
        self.vpn_card = self._tile(2, "VPN", self.toggle_vpn)

        # серверы VPN: выбор, пинг, «авто» (показывается, когда серверов больше одного)
        from .server_list import ServerPicker
        self.vpn_pings: Dict[str, Optional[float]] = {}
        self.vpn_pings_real = False
        self._ping_at = 0.0
        self._pinging = False
        self.servers_card = mark_card(ctk.CTkFrame(self, fg_color=W.P.surface, corner_radius=18))
        head = ctk.CTkFrame(self.servers_card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(head, text="Серверы VPN", font=W.title_font(16), anchor="w").pack(side="left")
        self.ping_note = ctk.CTkLabel(head, text="", text_color=W.GRAY, font=ctk.CTkFont(size=11))
        self.ping_note.pack(side="left", padx=(10, 0))
        self.ping_btn = W.add_button(head, "Проверить пинг", self.ping_servers, secondary=True, width=150)
        self.ping_btn.pack_configure(side="right", padx=0)
        # подписки обновляются и сами, но новые серверы бывает нужны сразу
        self.subs_btn = W.add_button(head, "⟳ Обновить серверы", lambda: self.app.pages["vpn"][1].refresh_subs(),
                                     secondary=True, width=170)
        self.subs_btn.pack_configure(side="right", padx=(0, 8))
        self.servers = ServerPicker(self.servers_card, app, compact=True)
        self.servers.pack(fill="x", padx=18, pady=(0, 14))
        self.pickers = [self.servers]

        self._avail: Dict[str, netcheck.ServiceState] = {}
        self._vpn_state = VpnState()
        self._zs, self._ts = ZapretStatus(), TgStatus()
        self._checking = False
        self._check_job = None
        self.after(1500, self._periodic_check)

    def _tile(self, column: int, title: str, on_toggle: Callable[[bool], None]) -> ServiceControl:
        control: ServiceControl
        tile = W.PowerTile(self.tiles, title, lambda: control.toggle())
        tile.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        self.tiles.grid_columnconfigure(column, weight=1, uniform="tiles")
        control = ServiceControl(tile, on_toggle)
        control.set_state(False, "…", W.GRAY, "")
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
        if error and self.app.winfo_viewable():
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
        if error and self.app.winfo_viewable():
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
        vp = self.app.pages.get("vpn")
        if vp:
            vp[1].render()
        if error and self.app.winfo_viewable():
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
        if len(profiles) >= 2:
            if not self.servers_card.winfo_manager():
                self.servers_card.pack(fill="x", padx=W.PAGE_PADX, pady=(0, 14), after=self.tiles)
        elif self.servers_card.winfo_manager():
            self.servers_card.pack_forget()
        for picker in self.pickers:
            picker.render()
        has_subs = bool(core.settings.get("vpn", "subscriptions"))
        if has_subs != bool(self.subs_btn.winfo_manager()):
            if has_subs:
                self.subs_btn.pack(side="right", padx=(0, 8), pady=2, after=self.ping_btn)
            else:
                self.subs_btn.pack_forget()
        if vs.running:
            how = "весь трафик" if vs.mode == "tun" else "системный прокси"
            proto = "AmneziaWG" if vs.kind == "awg" else "VLESS"
            details = f"{proto} · {how}"
            if vs.singbox and vs.singbox.running and core.settings.get("vpn", "ru_direct"):
                details += " · российское — напрямую"
            if vs.kind == "vless" and vs.current:
                name = self._server_name(vs.current)
                ms = self.vpn_pings.get(vs.current)
                name_ping = name + (f" · {ms:.0f} мс" if ms else "")
                title = ("Авто: " if vs.selected == "auto" else "") + name_ping
            else:
                name = active.name if active else "—"
                title = name
            self.vpn_card.set_state(True, f"● Подключён: {title}", W.GREEN, details,
                                    tile_details=f"{title}\n{details}", tile_status="● Подключён")
        elif auto:
            details = "" if core.singbox.is_installed() else "При первом включении скачается движок VPN с GitHub."
            self.vpn_card.set_state(False, "○ Выключен", W.GRAY, details,
                                    tile_details="Авто — лучший сервер")
        elif active is None:
            self.vpn_card.set_state(False, "○ Нет серверов", W.GRAY,
                                    "Добавьте ключ VLESS или ключ AmneziaVPN ниже.",
                                    tile_details="Добавьте свой ключ во вкладке VPN")
        else:
            engine = core.awg if active.kind == "awg" else core.singbox
            details = "" if engine.is_installed() else "При первом включении скачается движок VPN с GitHub."
            self.vpn_card.set_state(False, "○ Выключен", W.GRAY, details,
                                    tile_details=f"Сервер: {active.name}")

    def select_server(self, pid: str) -> None:
        """Выбор сервера (или «auto») на главной или во вкладке VPN."""
        core = self.app.core
        if core.settings.get("vpn", "active") == pid:
            return
        core.vpn_select(pid)
        self._render_vpn()
        vp = self.app.pages.get("vpn")
        if vp:
            vp[1].render()
        if self._vpn_state.running and not self.vpn_card.busy:
            self.vpn_card.set_busy(True, "Переключение…")
            self.app.run_task(core.vpn_switch, lambda r, e: self._vpn_done(e), "VPN")

    PING_MAX_AGE = 300  # с: при открытии главной/вкладки VPN пинг обновляется, если старше

    def ping_servers(self, max_age: float = 0) -> None:
        import time
        core = self.app.core
        if self._pinging or not core.vpn_vless() or time.time() - self._ping_at < max_age:
            return
        self._pinging = True
        for btn in self._ping_buttons():
            btn.configure(text="Проверка…", state="disabled")

        def done(result, err):
            self._pinging = False
            self._ping_at = time.time()
            for btn in self._ping_buttons():
                btn.configure(text="Проверить пинг", state="normal")
            if not err and result:
                self.vpn_pings, self.vpn_pings_real = dict(result[0]), result[1]
            self.ping_note.configure(text=("через VPN" if self.vpn_pings_real else "до сервера")
                                     if self.vpn_pings else "")
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
        self.refresh_btn.configure(text="Проверка…", state="disabled")
        self.on_status(self._zs, self._ts)  # на кнопках — «проверка…»
        self.app.run_task(netcheck.check_services, self._services_done)

    def _services_done(self, states, error) -> None:
        self._checking = False
        self.refresh_btn.configure(text="⟳  Обновить", state="normal")
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
            if st.ok:
                parts.append(f"{name}: доступен")
            elif st.partial:
                parts.append(f"{name}: частично")
            else:
                parts.append(f"{name}: недоступен")
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
            self.zapret_card.set_state(False, "Не установлен", W.GRAY,
                                       "Загрузка начнётся автоматически.", tile_details="Скачивается…")
        elif zs.running:
            strategy = (zs.service_strategy + ".bat") if zs.service_strategy else core.strategy
            details = f"Стратегия: {strategy} · режим: {mode} · версия {zs.version}"
            if zs.foreign_processes:
                details += "\nВнимание: запущен winws.exe из другой папки: " + ", ".join(zs.foreign_processes)
            self.zapret_card.set_state(True, "● Работает", W.GREEN, with_avail(details, avail_z),
                                       tile_details=with_avail(strategy.removesuffix(".bat"), avail_z))
        elif zs.service_installed:
            self.zapret_card.set_state(True, "⚠ Служба не работает", W.YELLOW,
                                       f"Служба установлена, но не работает ({zs.service_state}). "
                                       "Попробуйте другую стратегию или запустите диагностику.",
                                       tile_details="Выберите другую стратегию во вкладке Zapret")
        else:
            self.zapret_card.set_state(False, "○ Выключен", W.GRAY, with_avail(f"Версия {zs.version}", avail_z),
                                       tile_details=avail_z)

        if not ts.installed:
            self.tg_card.set_state(False, "Не установлен", W.GRAY, "Загрузка начнётся автоматически.",
                                   tile_details="Скачивается…")
        elif ts.running:
            cfg = core.tg.load_config()
            addr = f"{cfg.get('host')}:{cfg.get('port')}"
            if ts.listening:
                self.tg_card.set_state(True, f"● Работает на {addr}", W.GREEN,
                                       with_avail(f"Версия {ts.version}", avail_t),
                                       tile_details=with_avail(addr, avail_t), tile_status="● Работает")
            else:
                self.tg_card.set_state(True, "⚠ Порт не отвечает", W.YELLOW,
                                       "Процесс запущен, но порт не отвечает. Проверьте лог прокси ниже.",
                                       tile_details="Проверьте лог прокси во вкладке TG WS Proxy")
        else:
            details = f"Версия {ts.version}"
            if ts.foreign:
                details += "\nЗапущена другая копия TgWsProxy — закройте её перед включением: " + \
                           ", ".join(ts.foreign)
            self.tg_card.set_state(False, "○ Выключен", W.GRAY, with_avail(details, avail_t),
                                   tile_details=avail_t)

    def on_infos(self, infos: List[UpdateInfo]) -> None:
        pending = [i for i in infos if i.available and i.installed]
        if pending:
            text = "Доступны обновления: " + ", ".join(f"{i.title} {i.installed} → {i.latest}" for i in pending)
            self.banner_label.configure(text=text)
            self.banner.pack(fill="x", padx=W.PAGE_PADX, pady=(0, 14), before=self.tiles)
        else:
            self.banner.pack_forget()
