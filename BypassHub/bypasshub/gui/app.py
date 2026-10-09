from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
from tkinter import messagebox
from typing import Any, Callable, List, Optional

import tkinter as tk

import customtkinter as ctk

from .. import APP_NAME, __version__
from ..core import Core, UpdateInfo
from ..log import log
from ..paths import resource_path
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import theme as T
from . import widgets as W
from .surface import GradientPanel, build_mapping, crossfade, recolor_tree

NAV = [
    ("home", "Главная", "home"),
    ("zapret", "Zapret", "shield"),
    ("tg", "TG WS Proxy", "send"),
    ("updates", "Обновления", "sync"),
    ("settings", "Настройки", "settings"),
    ("log", "Журнал", "list"),
]


def set_dark_titlebar(window, dark: bool) -> None:
    """Тёмный или светлый заголовок окна Windows в цвет темы."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (новые и старые сборки)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception as exc:
        log.debug("dark titlebar: %r", exc)


class App(ctk.CTk):
    STATUS_INTERVAL = 3.0

    def __init__(self, core: Core, start_minimized: bool = False):
        self.core = core
        W.apply_theme(T.make_palette(core.settings.data["appearance"]))
        super().__init__()
        # картинки интерфейса рисуются сразу в масштабе экрана — так они чёткие
        T.SCALE = ctk.ScalingTracker.get_widget_scaling(self)
        self.title(APP_NAME)
        self.geometry("1040x720")
        self.minsize(900, 600)
        try:
            self.iconbitmap(str(resource_path("assets/icon.ico")))
        except Exception:
            pass

        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._closing = False
        self.zapret_status = ZapretStatus()
        self.tg_status = TgStatus()
        self.last_infos: List[UpdateInfo] = []
        self._status_wakeup = threading.Event()
        self.tray = None
        self.pages: dict = {}
        self.current_page = "home"

        self._build()
        set_dark_titlebar(self, W.P.mode == "dark")
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", lambda e: self._on_wheel(e, 120), add="+")
        self.bind_all("<Button-5>", lambda e: self._on_wheel(e, -120), add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._drain_queue)
        self.after(500, self._check_show_flag)

        threading.Thread(target=self._status_loop, daemon=True, name="status").start()
        threading.Thread(target=self._startup, daemon=True, name="startup").start()

        self._init_tray()
        from ..hotkeys import HotkeyManager
        self.hotkeys = HotkeyManager(lambda action: self.call_ui(lambda: self._on_hotkey(action)))
        self.pages["settings"][1]._hk_render(self.apply_hotkeys())
        if start_minimized and self.tray is not None:
            self.withdraw()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        from .home_page import HomePage
        from .log_page import LogPage
        from .settings_page import SettingsPage
        from .tg_page import TgPage
        from .updates_page import UpdatesPage
        from .zapret_page import ZapretPage

        P = W.P
        self.configure(fg_color=P.bg)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scale = ctk.ScalingTracker.get_widget_scaling(self)
        self.nav = GradientPanel(self, width=int(216 * scale))
        self.nav.grid(row=0, column=0, sticky="ns")
        self.nav.pack_propagate(False)
        W.GradientText(self.nav, APP_NAME, 22).pack(padx=20, pady=(24, 0), anchor="w")
        ctk.CTkLabel(self.nav, text="Zapret · TG WS Proxy", text_color=P.muted,
                     font=ctk.CTkFont(size=12)).pack(padx=20, pady=(0, 20), anchor="w")

        self.content = tk.Frame(self, bd=0, highlightthickness=0, bg=P.bg)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        classes = {"home": HomePage, "zapret": ZapretPage, "tg": TgPage, "updates": UpdatesPage,
                   "settings": SettingsPage, "log": LogPage}
        self.nav_items = {}
        self._page_palette = {}
        for key, label, icon in NAV:
            self.pages[key] = (label, classes[key](self.content, self))
            self._page_palette[key] = P
            item = W.NavItem(self.nav, label, icon, command=lambda k=key: self.show_page(k))
            item.pack(padx=16, pady=2)
            self.nav_items[key] = item

        ctk.CTkLabel(self.nav, text=f"v{__version__}", text_color=P.muted,
                     font=ctk.CTkFont(size=11)).pack(side="bottom", pady=14)
        self.show_page(self.current_page)

    def apply_appearance(self, animate: bool = True) -> None:
        """Сменить оформление без пересоздания окна: цвета заменяются на месте,
        переход сглаживается растворением снимка старого вида."""
        old = W.P
        new = T.make_palette(self.core.settings.data["appearance"])
        if new == old:
            return

        def apply() -> None:
            W.apply_theme(new, set_mode=new.mode != old.mode)
            mapping = build_mapping(old, new)
            self.configure(fg_color=new.bg)
            self.content.configure(bg=new.bg)
            recolor_tree(self.nav, mapping)
            for child in self.winfo_children():  # открытые диалоги
                if isinstance(child, ctk.CTkToplevel):
                    recolor_tree(child, mapping)
            self._recolor_page(self.current_page)
            set_dark_titlebar(self, new.mode == "dark")
            self.update_idletasks()

        if animate:
            crossfade(self, apply)
        else:
            apply()

    def _recolor_page(self, key: str) -> None:
        """Страницы перекрашиваются при показе — так смена темы не тормозит."""
        if self._page_palette.get(key) is W.P:
            return
        page = self.pages[key][1]
        recolor_tree(page, build_mapping(self._page_palette[key], W.P))
        self._page_palette[key] = W.P
        if hasattr(page, "on_palette"):
            page.on_palette()

    def show_page(self, key: str) -> None:
        self.current_page = key
        for k, (_, page) in self.pages.items():
            if k == key:
                page.grid(row=0, column=0, sticky="nsew")
                self._recolor_page(k)
                if hasattr(page, "on_show"):
                    page.on_show()
            else:
                page.grid_forget()
            self.nav_items[k].set_active(k == key)

    def _on_wheel(self, event, delta: Optional[int] = None):
        try:
            if event.widget.winfo_class() in ("Text", "Listbox"):
                return None  # у полей ввода своя прокрутка
        except Exception:
            pass
        page = self.pages.get(self.current_page, (None, None))[1]
        if hasattr(page, "scroll_units"):
            d = delta if delta is not None else event.delta
            page.scroll_units(-60 if d > 0 else 60)
        return None

    def report_callback_exception(self, exc, val, tb):
        # не даём ошибке обработчика уронить окно и не показываем её в журнале интерфейса
        log.error("Ошибка в интерфейсе:\n%s", "".join(traceback.format_exception(exc, val, tb)),
                  extra={"no_ui": True})

    # ------------------------------------------------------------------ потоки
    def call_ui(self, fn: Callable[[], None]) -> None:
        """Выполнить fn в потоке интерфейса (из любого потока)."""
        self._queue.put(fn)

    def _drain_queue(self) -> None:
        try:
            while True:
                fn = self._queue.get_nowait()
                try:
                    fn()
                except Exception:
                    log.error("Ошибка в интерфейсе:\n%s", traceback.format_exc(), extra={"no_ui": True})
        except queue.Empty:
            pass
        if not self._closing:
            self.after(100, self._drain_queue)

    def run_task(self, fn: Callable[[], Any], on_done: Optional[Callable[[Any, Optional[BaseException]], None]] = None,
                 error_title: Optional[str] = None) -> None:
        """Запуск долгой операции в фоне; on_done(result, error) вызывается в потоке UI."""
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
                self.call_ui(lambda zs=zs, ts=ts: self._apply_status(zs, ts))
            except Exception as exc:
                log.debug("status: %r", exc)
            self._status_wakeup.wait(self.STATUS_INTERVAL)
            self._status_wakeup.clear()

    def _apply_status(self, zs: ZapretStatus, ts: TgStatus) -> None:
        self.zapret_status, self.tg_status = zs, ts
        for _, page in self.pages.values():
            if hasattr(page, "on_status"):
                page.on_status(zs, ts)
        if self.tray is not None:
            self.tray.update(zs.running, ts.running)

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
        threading.Thread(target=self._update_timer, daemon=True, name="update-timer").start()

    def _update_timer(self) -> None:
        while not self._closing:
            time.sleep(60)
            hours = float(self.core.settings.get("updates", "interval_hours") or 0)
            last = float(self.core.settings.get("updates", "last_check") or 0)
            if hours > 0 and time.time() - last >= hours * 3600:
                self.run_updates(background=True)

    def run_updates(self, force_install: bool = False, background: bool = False) -> None:
        self.call_ui(lambda: self._run_updates_ui(force_install, background))

    def _run_updates_ui(self, force_install: bool, background: bool) -> None:
        updates: Any = self.pages["updates"][1]
        if getattr(updates, "busy", False):
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
            if self.core.restarting_for_update:  # установлена новая версия BypassHub — она уже запускается
                self.quit_app()
                return
            page.on_infos(infos)
            self.pages["home"][1].on_infos(infos)
            self.pages["zapret"][1].reload()
            pending = [i for i in infos if i.available]
            if pending and background and self.tray is not None:
                self.tray.notify("Доступны обновления: " + ", ".join(f"{i.title} {i.latest}" for i in pending))
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
        elif action == "show_window":
            if self.winfo_viewable() and self.focus_displayof() is not None:
                self.withdraw() if self.tray is not None else self.iconify()
            else:
                self.show_window()
        elif action == "check_services":
            home.check_services()
            if not self.winfo_viewable():
                self.show_window()

    def notify_if_hidden(self, text: str) -> None:
        """Уведомление в трее, когда действие выполнено, а окно скрыто (например, горячей клавишей)."""
        if self.tray is not None and not self.winfo_viewable():
            self.tray.notify(text)

    # ------------------------------------------------------------------ диалоги
    def info(self, text: str, title: Optional[str] = None) -> None:
        messagebox.showinfo(title or APP_NAME, text, parent=self)

    def error(self, text: str, title: Optional[str] = None) -> None:
        messagebox.showerror(title or APP_NAME, text, parent=self)

    def ask(self, text: str, title: Optional[str] = None) -> bool:
        return messagebox.askyesno(title or APP_NAME, text, parent=self)

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    # ------------------------------------------------------------------ трей и окно
    def _init_tray(self) -> None:
        try:
            from ..tray import Tray
            self.tray = Tray(self)
            self.tray.start()
        except Exception as exc:
            log.warning("Иконка в трее недоступна: %r", exc)
            self.tray = None

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    def _check_show_flag(self) -> None:
        flag = self.core.paths.show_flag
        if flag.exists():
            try:
                flag.unlink()
            except OSError:
                pass
            self.show_window()
        if not self._closing:
            self.after(700, self._check_show_flag)

    def on_close(self) -> None:
        if self.core.settings.get("app", "close_to_tray") and self.tray is not None:
            self.withdraw()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                self.tray.notify("BypassHub работает в трее. Выход — через меню значка.")
            return
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
            self.tray.stop()
        self.destroy()
