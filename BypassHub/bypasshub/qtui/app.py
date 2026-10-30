"""Главное окно на Qt: боковое меню, страницы, фоновые задачи, трей и горячие клавиши.

Логика та же, что у прежнего окна (bypasshub/gui/app.py): статус сервисов опрашивается
в отдельном потоке раз в 3 с, долгие операции идут в фоне (run_task), а всё, что трогает
интерфейс, выполняется в потоке окна (call_ui).
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Qt, Signal, qVersion
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMenu, QMessageBox,
                               QScrollArea, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import APP_NAME, SUPPORT_URL, __version__, winutil
from .. import palette as PL
from ..core import Core, UpdateInfo, VpnState
from ..log import log
from ..paths import resource_path
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import style as S
from . import widgets as W

NAV = [
    ("home", "Главная", "home"),
    ("zapret", "Zapret", "shield"),
    ("tg", "TG WS Proxy", "send"),
    ("vpn", "VPN", "vpn"),
    ("updates", "Обновления", "sync"),
    ("settings", "Настройки", "settings"),
    ("log", "Журнал", "list"),
]


class _Bridge(QObject):
    """Передаёт функции из любого потока в поток окна."""
    call = Signal(object)


class Page(QScrollArea):
    """Прокручиваемая страница: фон окна просвечивает, содержимое — в self.lay."""

    def __init__(self, app: "App"):
        super().__init__()
        self.app = app
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.lay = QVBoxLayout(self.content)
        self.lay.setContentsMargins(4, 8, W.PAGE_MARGIN, 18)
        self.lay.setSpacing(12)
        self.setWidget(self.content)
        self.viewport().setAutoFillBackground(False)
        self.content.setAutoFillBackground(False)

    def finish(self) -> None:
        """Конец содержимого: всё прижато к верху."""
        self.lay.addStretch(1)


def _app_icon() -> QIcon:
    return QIcon(str(resource_path("assets/icon.ico")))


def tray_icon(zapret_on: bool, tg_on: bool, vpn_on: bool) -> QIcon:
    """Значок трея со статусной точкой — тот же рисунок, что у прежнего трея (Pillow)."""
    from PIL.ImageQt import ImageQt
    from ..tray import make_icon
    img = make_icon(zapret_on, tg_on, vpn_on)
    return QIcon(QPixmap.fromImage(ImageQt(img)))


class App(QMainWindow):
    STATUS_INTERVAL = 3.0

    def __init__(self, core: Core, start_minimized: bool = False):
        super().__init__()
        self.core = core
        self._bridge = _Bridge()
        self._bridge.call.connect(self._run_call, Qt.ConnectionType.QueuedConnection)
        self._closing = False
        self.zapret_status = ZapretStatus()
        self.tg_status = TgStatus()
        self.vpn_state = VpnState()
        self.last_infos: List[UpdateInfo] = []
        self._status_wakeup = threading.Event()
        self.tray: Optional[QSystemTrayIcon] = None
        self.pages: Dict[str, Tuple[str, Any]] = {}
        self.current_page = "home"
        self._tray_state = (None, None, None)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(_app_icon())
        self.resize(1080, 740)
        self.setMinimumSize(920, 600)
        self.apply_appearance(initial=True)
        self._build()

        self._show_timer = QTimer(self)
        self._show_timer.timeout.connect(self._check_show_flag)
        self._show_timer.start(700)

        threading.Thread(target=self._status_loop, daemon=True, name="status").start()
        threading.Thread(target=self._startup, daemon=True, name="startup").start()

        self._init_tray()
        from ..hotkeys import HotkeyManager
        self.hotkeys = HotkeyManager(lambda action: self.call_ui(lambda: self._on_hotkey(action)))
        self.pages["settings"][1].hk_render(self.apply_hotkeys())
        if start_minimized and self.tray is not None:
            self.hide()
        else:
            self.show()

    # ------------------------------------------------------------------ раскладка
    def _build(self) -> None:
        from .home import HomePage
        from .log_page import LogPage
        from .settings_page import SettingsPage
        from .tg_page import TgPage
        from .updates_page import UpdatesPage
        from .vpn_page import VpnPage
        from .zapret_page import ZapretPage

        root = W.GradientBackground()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.nav = W.GradientBackground(side=True)
        self.nav.setFixedWidth(220)
        nav_lay = QVBoxLayout(self.nav)
        nav_lay.setContentsMargins(16, 22, 16, 14)
        nav_lay.setSpacing(3)
        self.logo = W.GradientText(APP_NAME, 17)
        nav_lay.addWidget(self.logo)
        sub = W.label("Zapret · TG WS Proxy · VPN", "small")
        nav_lay.addWidget(sub)
        nav_lay.addSpacing(16)
        lay.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay.addWidget(self.stack, 1)

        classes = {"home": HomePage, "zapret": ZapretPage, "tg": TgPage, "vpn": VpnPage, "updates": UpdatesPage,
                   "settings": SettingsPage, "log": LogPage}
        self.nav_items: Dict[str, W.NavButton] = {}
        for key, text, icon in NAV:
            page = classes[key](self)
            self.pages[key] = (text, page)
            self.stack.addWidget(page)
            item = W.NavButton(text, icon)
            item.clicked.connect(lambda _=False, k=key: self.show_page(k))
            nav_lay.addWidget(item)
            self.nav_items[key] = item
        nav_lay.addStretch(1)
        support = W.NavButton("Поддержать проект", "heart")
        support.setCheckable(False)
        support.clicked.connect(lambda: winutil.open_url(SUPPORT_URL))
        nav_lay.addWidget(support)
        ver = W.label(f"v{__version__}", "small")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_lay.addWidget(ver)
        self._restyle_nav()
        self.show_page(self.current_page)

    def _restyle_nav(self) -> None:
        for item in getattr(self, "nav_items", {}).values():
            item.restyle()

    def show_page(self, key: str) -> None:
        self.current_page = key
        page = self.pages[key][1]
        for k, item in self.nav_items.items():
            if item.isChecked() != (k == key):
                item.setChecked(k == key)
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()

    # ------------------------------------------------------------------ оформление
    def apply_appearance(self, animate: bool = True, initial: bool = False) -> None:
        """Сменить оформление: новая таблица стилей для всего приложения, Qt перерисует окно сам."""
        new = PL.make_palette(self.core.settings.data["appearance"])
        if not initial and new == S.P:
            return
        S.set_palette(new)
        app = QApplication.instance()
        app.setStyleSheet(S.stylesheet(new))
        if not initial:
            self._restyle_nav()
            for _, page in self.pages.values():
                if hasattr(page, "on_palette"):
                    page.on_palette()
            for w in app.allWidgets():
                w.update()
        set_dark_titlebar(self, new.mode == "dark")

    # ------------------------------------------------------------------ потоки
    def call_ui(self, fn: Callable[[], None]) -> None:
        """Выполнить fn в потоке окна (из любого потока)."""
        self._bridge.call.emit(fn)

    def _run_call(self, fn) -> None:
        try:
            fn()
        except Exception:
            log.error("Ошибка в интерфейсе:\n%s", traceback.format_exc(), extra={"no_ui": True})

    def run_task(self, fn: Callable[[], Any], on_done: Optional[Callable[[Any, Optional[BaseException]], None]] = None,
                 error_title: Optional[str] = None) -> None:
        """Долгая операция в фоне; on_done(result, error) вызывается в потоке окна."""
        def worker():
            result, error = None, None
            try:
                result = fn()
            except BaseException as exc:  # noqa: B902
                error = exc
                log.error("%s: %s", error_title or "Ошибка", exc)
                log.debug(traceback.format_exc())

            def finish():
                if on_done:
                    on_done(result, error)
                elif error is not None:
                    self.error(str(error), error_title)
                self.refresh_status()
            self.call_ui(finish)
        threading.Thread(target=worker, daemon=True).start()

    def refresh_status(self) -> None:
        self._status_wakeup.set()

    def _status_loop(self) -> None:
        while not self._closing:
            try:
                zs = self.core.zapret.status()
                ts = self.core.tg.status()
                vs = self.core.vpn_state()
                self.call_ui(lambda zs=zs, ts=ts, vs=vs: self._apply_status(zs, ts, vs))
            except Exception as exc:
                log.debug("status: %r", exc)
            self._status_wakeup.wait(self.STATUS_INTERVAL)
            self._status_wakeup.clear()

    def _apply_status(self, zs: ZapretStatus, ts: TgStatus, vs: VpnState) -> None:
        self.zapret_status, self.tg_status, self.vpn_state = zs, ts, vs
        for _, page in self.pages.values():
            if hasattr(page, "on_status"):
                page.on_status(zs, ts)
            if hasattr(page, "on_vpn_status"):
                page.on_vpn_status(vs)
        self._update_tray(zs.running, ts.running, vs.running)

    # ------------------------------------------------------------------ запуск и обновления
    def _startup(self) -> None:
        core = self.core
        core.cleanup()
        try:
            core.restore_state()
        except Exception as exc:
            log.error("Восстановление состояния: %s", exc)
        self.refresh_status()
        need_install = not core.zapret.is_installed() or not core.tg.is_installed()
        if core.settings.get("updates", "check_on_start") or need_install:
            self.run_updates()
        self._refresh_subs()
        threading.Thread(target=self._update_timer, daemon=True, name="update-timer").start()

    def _update_timer(self) -> None:
        while not self._closing:
            time.sleep(60)
            self._refresh_subs()
            hours = float(self.core.settings.get("updates", "interval_hours") or 0)
            last = float(self.core.settings.get("updates", "last_check") or 0)
            if hours > 0 and time.time() - last >= hours * 3600:
                self.run_updates(background=True)

    def _refresh_subs(self) -> None:
        """Подписки VPN обновляются сами (при запуске и раз в несколько часов, как в Happ)."""
        core = self.core
        if not core.settings.get("vpn", "subscriptions"):
            return
        before = [p.to_dict() for p in core.vpn_profiles()]
        try:
            core.vpn_refresh_subscriptions(only_due=True)
        except Exception as exc:
            log.debug("Подписки VPN: %r", exc)
            return
        after = core.vpn_profiles()
        if [p.to_dict() for p in after] != before:
            log.info("Подписки VPN обновлены, серверов: %d", len(after))
            self.call_ui(self.subscriptions_changed)

    def subscriptions_changed(self) -> None:
        self.pages["vpn"][1].render()
        self.pages["home"][1].reload_vpn()

    def run_updates(self, force_install: bool = False, background: bool = False) -> None:
        self.call_ui(lambda: self._run_updates_ui(force_install, background))

    def _run_updates_ui(self, force_install: bool, background: bool) -> None:
        updates: Any = self.pages["updates"][1]
        if updates.busy:
            return
        updates.set_busy(True, "Проверка обновлений…")

        def progress(component: str, done: int, total: int) -> None:
            self.call_ui(lambda: self.pages["updates"][1].on_progress(component, done, total))

        def work():
            return self.core.update_cycle(force_install=force_install, progress=progress)

        def done(infos, error):
            page: Any = self.pages["updates"][1]
            page.set_busy(False)
            if error:
                page.show_error(str(error))
                return
            self.last_infos = infos
            if self.core.restarting_for_update:  # новая версия BypassHub уже запускается
                self.quit_app()
                return
            page.on_infos(infos)
            self.pages["home"][1].on_infos(infos)
            self.pages["zapret"][1].reload()
            self.pages["vpn"][1].render()
            pending = [i for i in infos if i.available]
            if pending and background:
                self.notify("Доступны обновления: " + ", ".join(f"{i.title} {i.latest}" for i in pending))
        self.run_task(work, done)

    # ------------------------------------------------------------------ горячие клавиши
    def apply_hotkeys(self) -> list:
        h = self.core.settings.data["hotkeys"]
        return self.hotkeys.apply(bool(h.get("enabled")), dict(h.get("bindings") or {}))

    def _on_hotkey(self, action: str) -> None:
        home: Any = self.pages["home"][1]
        if action == "toggle_zapret":
            home.toggle_zapret_from_tray()
        elif action == "toggle_tg":
            home.toggle_tg_from_tray()
        elif action == "toggle_vpn":
            home.toggle_vpn_from_tray()
        elif action == "show_window":
            if self.isVisible() and self.isActiveWindow():
                self.hide() if self.tray is not None else self.showMinimized()
            else:
                self.show_window()
        elif action == "check_services":
            home.check_services()
            if not self.isVisible():
                self.show_window()

    def open_phone_dialog(self) -> None:
        from .dialogs import PhoneDialog
        dlg = getattr(self, "_phone_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        self._phone_dialog = PhoneDialog(self)
        self._phone_dialog.show()

    def is_shown(self) -> bool:
        return self.isVisible() and not self.isMinimized()

    def notify_if_hidden(self, text: str) -> None:
        """Уведомление в трее, когда действие выполнено, а окно скрыто (например, горячей клавишей)."""
        if not self.is_shown():
            self.notify(text)

    # ------------------------------------------------------------------ диалоги
    def info(self, text: str, title: Optional[str] = None) -> None:
        QMessageBox.information(self, title or APP_NAME, text)

    def error(self, text: str, title: Optional[str] = None) -> None:
        QMessageBox.critical(self, title or APP_NAME, text)

    def ask(self, text: str, title: Optional[str] = None) -> bool:
        res = QMessageBox.question(self, title or APP_NAME, text,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.Yes)
        return res == QMessageBox.StandardButton.Yes

    def copy_to_clipboard(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def clipboard_text(self) -> str:
        return QGuiApplication.clipboard().text()

    # ------------------------------------------------------------------ трей и окно
    def _init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("Иконка в трее недоступна")
            return
        self.tray = QSystemTrayIcon(tray_icon(False, False, False), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        home = lambda: self.pages["home"][1]  # noqa: E731
        menu.addAction("Открыть BypassHub", self.show_window)
        menu.addSeparator()
        self._tray_actions = {}
        for key, text, fn in (("zapret", "Zapret", lambda: home().toggle_zapret_from_tray()),
                              ("tg", "TG WS Proxy", lambda: home().toggle_tg_from_tray()),
                              ("vpn", "VPN", lambda: home().toggle_vpn_from_tray())):
            act = QAction(text, self)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, f=fn: f())
            menu.addAction(act)
            self._tray_actions[key] = act
        menu.addAction("Открыть прокси в Telegram", lambda: home().open_in_telegram())
        menu.addSeparator()
        menu.addAction("Проверить обновления", lambda: self.run_updates())
        menu.addAction("Выход", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _update_tray(self, zapret_on: bool, tg_on: bool, vpn_on: bool) -> None:
        state = (bool(zapret_on), bool(tg_on), bool(vpn_on))
        if self.tray is None or state == self._tray_state:
            return
        self._tray_state = state
        self.tray.setIcon(tray_icon(*state))
        self.tray.setToolTip(f"{APP_NAME}\nZapret: {'вкл' if state[0] else 'выкл'}\n"
                             f"TG Proxy: {'вкл' if state[1] else 'выкл'}\nVPN: {'вкл' if state[2] else 'выкл'}")
        for key, on in zip(("zapret", "tg", "vpn"), state):
            self._tray_actions[key].setChecked(on)

    def notify(self, text: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(APP_NAME, text, _app_icon(), 5000)

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _check_show_flag(self) -> None:
        flag = self.core.paths.show_flag
        if flag.exists():
            try:
                flag.unlink()
            except OSError:
                pass
            self.show_window()
        from ..hotkeys import ACTIONS
        for action in ACTIONS:  # BypassHub --action … (свои горячие клавиши в Linux)
            flag = self.core.paths.action_flag(action)
            if flag.exists():
                try:
                    flag.unlink()
                except OSError:
                    continue
                self._on_hotkey(action)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — имя Qt
        if self._closing:
            event.accept()
            return
        if self.core.settings.get("app", "close_to_tray") and self.tray is not None:
            event.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                self.notify("BypassHub работает в трее. Выход — через меню значка.")
            return
        event.ignore()
        self.quit_app()

    def quit_app(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._status_wakeup.set()
        log.info("Выход из BypassHub")
        try:
            self.core.shutdown()
        except Exception as exc:
            log.warning("shutdown: %r", exc)
        self.hotkeys.stop()
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()


def set_dark_titlebar(window: QWidget, dark: bool) -> None:
    """Тёмный или светлый заголовок окна Windows в цвет темы."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (новые и старые сборки)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception as exc:
        log.debug("dark titlebar: %r", exc)


def run(core: Core, start_minimized: bool = False) -> int:
    """Запустить окно Qt и цикл событий; возвращает код выхода."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication.instance() or QApplication([])
    qapp.setApplicationName(APP_NAME)
    qapp.setDesktopFileName("bypasshub")  # Wayland: значок окна и панели — из ярлыка bypasshub.desktop
    qapp.setQuitOnLastWindowClosed(False)  # окно прячется в трей, выход — через меню
    qapp.setFont(S.base_font())
    log.info("Интерфейс: Qt %s", qVersion())
    window = App(core, start_minimized=start_minimized)
    qapp._bh_window = window  # ссылка, чтобы окно не собрал GC
    return qapp.exec()
