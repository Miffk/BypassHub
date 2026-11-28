"""Окно на HTML: pywebview (в Windows — встроенный движок Edge WebView2), трей, горячие клавиши,
запуск и обновления — то же, что делает окно Qt (qtui/app.py), но для страницы webui."""
from __future__ import annotations

import threading
import time
from typing import Any, List

from .. import APP_NAME, winutil
from ..core import Core
from ..log import log
from ..paths import resource_path
from .controller import WebApp


class Api:
    """То, что страница может вызвать: window.pywebview.api.boot() / poll(страница) / act(…)."""

    def __init__(self, app: WebApp):
        self._app = app

    def boot(self):
        return self._app.boot()

    def poll(self, page):
        return self._app.poll(str(page or "home"))

    def act(self, page, action, value=None):
        return self._app.act(str(page or "home"), str(action or ""), value)


class _TrayHome:
    """Для трея (bypasshub/tray.py): то, что он вызывает у главной страницы окна."""

    def __init__(self, app: "WindowApp"):
        self.app = app

    def toggle_zapret_from_tray(self):
        if "zapret" not in self.app.busy:
            self.app.toggle("zapret")

    def toggle_tg_from_tray(self):
        if "tg" not in self.app.busy:
            self.app.toggle("tg")

    def toggle_vpn_from_tray(self):
        if "vpn" not in self.app.busy:
            self.app.toggle("vpn")

    def open_in_telegram(self):
        from .pages.home import _tg_open
        res = _tg_open(self.app, None)
        if res.get("info"):
            self.app.alert(res["info"], "TG WS Proxy")
            self.app.show_window()


class WindowApp(WebApp):
    def __init__(self, core: Core, start_minimized: bool = False):
        super().__init__(core)
        self.start_minimized = start_minimized
        self.window: Any = None
        self.tray: Any = None
        self.hotkeys: Any = None
        self.pages_for_tray = {"home": ("", _TrayHome(self))}
        self._quitting = False
        self._tray_state = None

    def call_ui(self, fn):
        try:
            fn()
        except Exception as exc:
            log.error("Ошибка в интерфейсе: %s", exc)

    # ------------------------------------------------------------------ окно
    def show_window(self) -> None:
        if self.window is None:
            return
        self.shown = True
        try:
            self.window.show()
            self.window.restore()
        except Exception as exc:
            log.debug("show: %r", exc)
        self.refresh()

    def hide_window(self) -> None:
        self.shown = False
        try:
            self.window.hide()
        except Exception as exc:
            log.debug("hide: %r", exc)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("Выход из BypassHub")
        self.stop()
        try:
            self.core.shutdown()
        except Exception as exc:
            log.error("Выход: %s", exc)
        if self.hotkeys is not None:
            self.hotkeys.stop()
        if self.tray is not None:
            self.tray.stop()
        try:
            self.window.destroy()
        except Exception:
            pass

    quit_app = quit

    def _on_closing(self):
        """Кнопка «закрыть»: в трей (если включено и трей есть) — иначе выход."""
        if self._quitting:
            return True
        if self.core.settings.get("app", "close_to_tray") and self.tray is not None:
            threading.Thread(target=self.hide_window, daemon=True).start()
            return False
        threading.Thread(target=self.quit, daemon=True).start()
        return False

    def notify(self, text: str) -> None:
        if self.tray is not None:
            self.tray.notify(text)
        else:
            log.info("%s", text)

    # ------------------------------------------------------------------ системное
    def clipboard_set(self, value: str) -> None:
        from . import clip
        clip.set_text(value)

    def clipboard_get(self) -> str:
        from . import clip
        return clip.get_text()

    def pick_files(self, title: str, kinds: str = "", many: bool = False) -> List[str]:
        import webview
        types = tuple(t for t in (kinds, "Все файлы (*.*)") if t)
        try:
            res = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=many, file_types=types)
        except Exception as exc:
            log.error("Выбор файла: %s", exc)
            return []
        return list(res or [])

    def save_file(self, title: str, name: str) -> str:
        import webview
        try:
            res = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name,
                                                 file_types=("Настройки BypassHub (*.json)",))
        except Exception as exc:
            log.error("Сохранение файла: %s", exc)
            return ""
        if not res:
            return ""
        return res if isinstance(res, str) else res[0]

    def apply_hotkeys(self) -> List[str]:
        if self.hotkeys is None:
            return []
        h = self.core.settings.data["hotkeys"]
        return self.hotkeys.apply(bool(h.get("enabled")), dict(h.get("bindings") or {}))

    def _on_hotkey(self, action: str) -> None:
        home = _TrayHome(self)
        if action == "toggle_zapret":
            home.toggle_zapret_from_tray()
        elif action == "toggle_tg":
            home.toggle_tg_from_tray()
        elif action == "toggle_vpn":
            home.toggle_vpn_from_tray()
        elif action == "show_window":
            self.hide_window() if self.shown and self.tray is not None else self.show_window()
        elif action == "check_services":
            self.check_services()
            if not self.shown:
                self.show_window()

    def on_status(self, zs, ts, vs) -> None:
        state = (zs.running, ts.running, vs.active)
        if self.tray is not None and state != self._tray_state:
            self._tray_state = state
            self.tray.update(*state)

    # ------------------------------------------------------------------ запуск
    def _startup(self) -> None:
        core = self.core
        core.cleanup()
        try:
            core.restore_state()
        except Exception as exc:
            log.error("Восстановление состояния: %s", exc)
        self.refresh()
        if core.settings.get("updates", "check_on_start") or not core.zapret.is_installed() or not core.tg.is_installed():
            self.run_updates()
        self._refresh_subs()
        threading.Thread(target=self._timers, daemon=True, name="web-timers").start()

    def _refresh_subs(self) -> None:
        if not self.core.settings.get("vpn", "subscriptions"):
            return
        try:
            self.core.vpn_refresh_subscriptions(only_due=True)
        except Exception as exc:
            log.debug("Подписки VPN: %r", exc)

    def _timers(self) -> None:
        from ..hotkeys import ACTIONS
        last_minute = time.monotonic()
        while not self._closing:
            time.sleep(0.7)
            # другая копия BypassHub попросила показать окно, или пришло действие (BypassHub --action …)
            flag = self.core.paths.show_flag
            if flag.exists():
                try:
                    flag.unlink()
                except OSError:
                    pass
                self.show_window()
            for action in ACTIONS:
                f = self.core.paths.action_flag(action)
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        continue
                    self._on_hotkey(action)
            if time.monotonic() - last_minute >= 60:
                last_minute = time.monotonic()
                self._refresh_subs()
                hours = float(self.core.settings.get("updates", "interval_hours") or 0)
                last = float(self.core.settings.get("updates", "last_check") or 0)
                if hours > 0 and time.time() - last >= hours * 3600:
                    self.run_updates(background=True)

    def _started(self) -> None:
        """Окно создано: тёмный заголовок, трей, горячие клавиши, фон."""
        self._dark_titlebar()
        self.start()
        threading.Thread(target=self._startup, daemon=True, name="web-startup").start()

    def _dark_titlebar(self) -> None:
        if not winutil.IS_WINDOWS:
            return
        try:
            import ctypes
            hwnd = self.window.native.Handle.ToInt32()
            dark = ctypes.c_int(1 if self.theme()["mode"] == "dark" else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (новые и старые сборки Windows 10)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), 4) == 0:
                    break
        except Exception as exc:
            log.debug("тёмный заголовок: %r", exc)


def run(core: Core, start_minimized: bool = False) -> int:
    """Окно на HTML. Исключение (нет движка WebView2, не загрузился pywebview) — запуск откатится
    на окно Qt (main.py)."""
    import webview
    app = WindowApp(core, start_minimized)
    try:
        from ..tray import Tray
        adapter = _TrayAdapter(app)
        app.tray = Tray(adapter)
        app.tray.start()
    except Exception as exc:
        log.warning("Иконка в трее недоступна: %r", exc)
        app.tray = None
    try:
        from ..hotkeys import HotkeyManager
        app.hotkeys = HotkeyManager(app._on_hotkey)
        app.apply_hotkeys()
    except Exception as exc:
        log.debug("горячие клавиши: %r", exc)
    index = resource_path("bypasshub/webui/index.html")
    if not index.exists():
        index = resource_path("webui/index.html")
    hidden = bool(start_minimized and app.tray is not None)
    app.shown = not hidden
    bg = "#f2f2f6" if app.theme()["mode"] == "light" else "#07080b"
    app.window = webview.create_window(APP_NAME, url=index.as_uri(), js_api=Api(app), width=1180, height=800,
                                       min_size=(980, 660), hidden=hidden, background_color=bg, text_select=False)
    app.window.events.closing += app._on_closing
    log.info("Интерфейс: HTML (pywebview %s)", getattr(webview, "__version__", ""))
    storage = core.paths.root / "webview"
    try:
        webview.start(app._started, gui="edgechromium" if winutil.IS_WINDOWS else None, private_mode=False,
                      storage_path=str(storage), debug=False, icon=str(resource_path("assets/icon.ico")))
    except Exception:
        # окно не открылось (например, нет движка WebView2) — убираем трей и горячие клавиши,
        # main.py откроет прежнее окно
        if app.hotkeys is not None:
            app.hotkeys.stop()
        if app.tray is not None:
            app.tray.stop()
        app.stop()
        raise
    app.quit()
    return 0


class _TrayAdapter:
    """tray.Tray обращается к окну Qt (app.pages["home"][1], call_ui, show_window…) — даём то же."""

    def __init__(self, app: WindowApp):
        self._app = app
        self.pages = app.pages_for_tray

    def call_ui(self, fn):
        threading.Thread(target=lambda: self._app.call_ui(fn), daemon=True).start()

    def show_window(self):
        self._app.show_window()

    def run_updates(self, force_install: bool = False, background: bool = False):
        self._app.run_updates(force_install, background)

    def quit_app(self):
        self._app.quit()
