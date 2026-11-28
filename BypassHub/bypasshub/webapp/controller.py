"""Состояние окна на HTML и действия: то же, что делают страницы окна Qt, но без виджетов.

Страница (webui) раз в полторы секунды спрашивает poll(страница) и рисует ответ; нажатия приходят
в act(страница, действие, значение). Долгое (включить VPN, обновить) идёт в фоне: на странице —
«занято», по окончании — всплывающее сообщение или окно с ошибкой."""
from __future__ import annotations

import collections
import hashlib
import json
import threading
import time
import traceback
from typing import Any, Callable, Deque, Dict, List, Optional

from .. import __version__, netcheck, palette as PL, tgproxy, vpn, winutil
from ..core import Core, UpdateInfo, VpnState
from ..log import log
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import ui

NAV = [
    ("home", "Главная", "home"), ("zapret", "Zapret", "shield"), ("tg", "TG WS Proxy", "send"),
    ("vpn", "VPN", "vpn"), ("updates", "Обновления", "sync"), ("changelog", "Что нового", "news"),
    ("settings", "Настройки", "settings"), ("log", "Журнал", "list"),
]
PING_MAX_AGE = 300       # с: при открытии главной/VPN пинг обновляется, если старше
CHECK_PERIOD = 180       # с: доступность сервисов проверяется сама, пока окно открыто
HISTORY = 24             # точек на графике задержки


class WebApp:
    STATUS_INTERVAL = 3.0

    def __init__(self, core: Core):
        self.core = core
        self.zs, self.ts, self.vs = ZapretStatus(), TgStatus(), VpnState()
        self.busy: Dict[str, str] = {}             # что сейчас занято: «zapret», «vpn», «updates»… → надпись
        self.toasts: List[Dict[str, str]] = []
        self.alerts: List[Dict[str, str]] = []     # окна с ошибкой или сообщением — покажет страница
        self.avail: Dict[str, netcheck.ServiceState] = {}
        self.ip: Optional[netcheck.PublicIp] = None
        self.ip_at = ""
        self.pings: Dict[str, Optional[float]] = {}
        self.pings_real = False
        self.ping_at = 0.0
        self.history: Deque[float] = collections.deque(maxlen=HISTORY)
        self.vpn_since: Optional[float] = None
        self.infos: List[UpdateInfo] = []
        self.progress: Dict[str, str] = {}         # компонент → «12.3 из 40 МБ»
        self.page_state: Dict[str, Dict[str, Any]] = collections.defaultdict(dict)  # память страниц
        self.sheet: Optional[Dict[str, Any]] = None  # окно поверх страницы (подбор стратегии, диагностика)
        self.shown = True                          # окно на экране (не в трее)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closing = False
        self._last_check = 0.0
        self.window: Any = None                    # окно (pywebview) — для диалогов выбора файла и показа
        from . import pages
        self.pages = pages

    # ------------------------------------------------------------------ фон
    def run(self, key: str, fn: Callable[[], Any], done: Optional[Callable[[Any], Any]] = None,
            busy: str = "", title: str = "") -> bool:
        """fn — в фоне; key занят, пока идёт. Ошибка — окном; done(результат) — в конце (может вернуть
        текст для всплывающего сообщения). False — уже идёт."""
        with self._lock:
            if key in self.busy:
                return False
            self.busy[key] = busy or "…"

        def work():
            try:
                result = fn()
            except BaseException as exc:  # noqa: B902
                log.error("%s: %s", title or key, exc)
                log.debug(traceback.format_exc())
                self.alert(str(exc), title or "Ошибка", "error")
            else:
                if done is not None:
                    try:
                        msg = done(result)
                        if msg:
                            self.toast(msg)
                    except Exception as exc:
                        log.error("%s: %s", title or key, exc)
            finally:
                with self._lock:
                    self.busy.pop(key, None)
                self.refresh()
        threading.Thread(target=work, daemon=True, name=f"web-{key}").start()
        return True

    def toast(self, text: str, kind: str = "") -> None:
        with self._lock:
            self.toasts.append({"text": text, "kind": kind})

    def alert(self, text: str, title: str = "BypassHub", kind: str = "") -> None:
        with self._lock:
            self.alerts.append({"text": text, "title": title, "kind": kind})

    def refresh(self) -> None:
        self._wake.set()

    def start(self) -> None:
        threading.Thread(target=self._status_loop, daemon=True, name="web-status").start()

    def stop(self) -> None:
        self._closing = True
        self._wake.set()

    def _status_loop(self) -> None:
        while not self._closing:
            try:
                zs, ts, vs = self.core.zapret.status(), self.core.tg.status(), self.core.vpn_state()
                self._apply_status(zs, ts, vs)
            except Exception as exc:
                log.debug("status: %r", exc)
            if self.shown and time.time() - self._last_check > CHECK_PERIOD:
                self.check_services()
            self._wake.wait(self.STATUS_INTERVAL)
            self._wake.clear()

    def _apply_status(self, zs: ZapretStatus, ts: TgStatus, vs: VpnState) -> None:
        if vs.running and not self.vs.running:
            self.vpn_since = time.time() * 1000
            self.history.clear()
        elif not vs.running:
            self.vpn_since = None
        if vs.running and vs.kind == "vless" and vs.delays:
            self.pings.update({k: float(v) for k, v in vs.delays.items()})
            self.pings_real = True
            cur = self.pings.get(vs.current)
            if cur:
                self.history.append(cur)
        self.zs, self.ts, self.vs = zs, ts, vs
        on_status = getattr(self, "on_status", None)
        if on_status:
            on_status(zs, ts, vs)

    # ------------------------------------------------------------------ проверки
    def check_services(self) -> None:
        self._last_check = time.time()

        def work():
            return netcheck.check_services(), netcheck.public_ip()

        def done(res):
            states, ip = res
            self.avail = {st.name: st for st in states}
            if ip is not None:
                self.ip, self.ip_at = ip, time.strftime("%H:%M")
        self.run("check", work, done, "Проверка…", "Проверка доступности")

    def ping_servers(self, max_age: float = 0) -> None:
        core = self.core
        if not core.vpn_vless() or time.time() - self.ping_at < max_age:
            return

        def done(res):
            self.ping_at = time.time()
            if res:
                self.pings, self.pings_real = dict(res[0]), res[1]
        self.run("ping", core.vpn_ping, done, "Проверка…", "Пинг серверов")

    # ------------------------------------------------------------------ сервисы
    def toggle(self, service: str, on: Optional[bool] = None) -> None:
        core = self.core
        if service == "zapret":
            on = (not self.zs.running) if on is None else on
            self.run("zapret", core.zapret_enable if on else core.zapret_disable,
                     lambda _: self._after_toggle("Zapret", "zapret"), "Включение…" if on else "Выключение…", "Zapret")
        elif service == "tg":
            on = (not self.ts.running) if on is None else on
            self.run("tg", core.tg_enable if on else core.tg_disable,
                     lambda _: self._after_toggle("TG WS Proxy", "tg"), "Запуск…" if on else "Остановка…",
                     "TG WS Proxy")
        elif service == "vpn":
            on = (not self.vs.active) if on is None else on
            if on and not core.vpn_profiles():
                self.alert("Сначала добавьте свой сервер: вкладка VPN → вставьте ключ vless://… или ключ "
                           "AmneziaVPN vpn://…", "VPN")
                return

            def progress(component: str, done: int, total: int) -> None:
                self.busy["vpn"] = (f"Загрузка: {done / 1048576:.1f}" + (f" из {total / 1048576:.1f}" if total else "")
                                    + " МБ")
            fn = (lambda: core.vpn_enable(progress)) if on else core.vpn_disable
            self.run("vpn", fn, lambda _: self._after_toggle("VPN", "vpn"), "Подключение…" if on else "Отключение…",
                     "VPN")

    def _after_toggle(self, title: str, key: str) -> str:
        on = self.core.settings.get(key, "enabled")
        threading.Timer(2.5, self.check_services).start()
        return f"{title} включён" if on else f"{title} выключен"

    def apply_vpn(self) -> None:
        """Настройки VPN изменились — перезапустить его, если включён."""
        if self.vs.active:
            self.run("vpn", self.core.vpn_apply, None, "Применение настроек…", "VPN")

    def select_server(self, pid: str) -> None:
        core = self.core
        if core.settings.get("vpn", "active") == pid:
            return
        core.vpn_select(pid)
        if self.vs.active:
            self.run("vpn", core.vpn_switch, None, "Переключение…", "VPN")

    def apply_zapret(self, what: str) -> None:
        if self.zs.running or self.zs.service_installed:
            self.run("zapret", self.core.zapret_apply, None, f"Применение: {what}…", "Zapret")

    # ------------------------------------------------------------------ обновления
    def run_updates(self, force_install: bool = False, background: bool = False) -> None:
        def progress(component: str, done: int, total: int) -> None:
            self.progress[component] = (f"{done / 1048576:.1f}" + (f" из {total / 1048576:.1f}" if total else "")
                                        + " МБ")

        def done(infos):
            self.progress.clear()
            self.infos = infos
            if self.core.restarting_for_update:
                self.quit()
                return ""
            pending = [i for i in infos if i.available]
            if pending and background:
                self.notify("Доступны обновления: " + ", ".join(f"{i.title} {i.latest}" for i in pending))
            return ""
        self.run("updates", lambda: self.core.update_cycle(force_install=force_install, progress=progress), done,
                 "Проверка обновлений…", "Обновления")

    # окно и трей подставляет window.py
    def notify(self, text: str) -> None:
        log.info("%s", text)

    def quit(self) -> None:
        pass

    def show_window(self) -> None:
        pass

    def open_url(self, url: str) -> None:
        winutil.open_url(url)

    def clipboard_set(self, value: str) -> None:
        pass

    def clipboard_get(self) -> str:
        return ""

    def pick_files(self, title: str, kinds: str = "", many: bool = False) -> List[str]:
        return []

    def save_file(self, title: str, name: str) -> str:
        return ""

    def apply_hotkeys(self) -> List[str]:
        return []

    # ------------------------------------------------------------------ связь со страницей
    def theme(self) -> Dict[str, Any]:
        a = self.core.settings.data["appearance"]
        mode = a.get("mode", "dark")
        if mode == "system":
            mode = PL.system_mode()
        return {"mode": mode, "accents": PL.normalize_colors(a.get("colors")), "tint": int(a.get("tint", 70))}

    def dots(self) -> Dict[str, bool]:
        seen = self.core.settings.get("app", "seen_changelog") or ""
        if not seen:
            self.core.settings.set("app", "seen_changelog", __version__)
            seen = __version__
        return {"changelog": seen != __version__}

    def boot(self) -> Dict[str, Any]:
        expanded = self.core.settings.get("ui", "expanded") or []
        return {"nav": NAV, "version": __version__, "theme": self.theme(), "dots": self.dots(),
                "open": {k: True for k in expanded}, "page": "home", "interval": 1500}

    def poll(self, page: str) -> Dict[str, Any]:
        with self._lock:
            toasts, self.toasts = self.toasts, []
            alerts, self.alerts = self.alerts, []
        res: Dict[str, Any] = {"theme": self.theme(), "dots": self.dots(), "toasts": toasts, "alerts": alerts}
        try:
            if page == "home":
                res["home"] = self.pages.home_state(self)
            else:
                schema = self.pages.build(self, page)
                if self.sheet:
                    schema["sheet"] = self.sheet.schema()
                res["page"] = schema
                res["rev"] = hashlib.sha1(json.dumps(schema, ensure_ascii=False, sort_keys=True,
                                                     default=str).encode()).hexdigest()[:16]
        except Exception:
            log.error("Страница %s:\n%s", page, traceback.format_exc())
            res["page"] = ui.page("Ошибка", [ui.notice("Не удалось показать страницу — подробности в журнале.")])
        return res

    def act(self, page: str, action: str, value: Any = None) -> Dict[str, Any]:
        try:
            return self.pages.act(self, page, action, value) or {}
        except Exception as exc:
            log.error("Действие %s/%s: %s\n%s", page, action, exc, traceback.format_exc())
            return ui.error(str(exc))
