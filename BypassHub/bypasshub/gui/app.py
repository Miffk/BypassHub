from __future__ import annotations

import queue
import threading
import time
import traceback
from tkinter import messagebox
from typing import Any, Callable, List, Optional

import customtkinter as ctk

from .. import APP_NAME, __version__
from ..core import Core, UpdateInfo
from ..log import log
from ..paths import resource_path
from ..tgproxy import TgStatus
from ..zapret import ZapretStatus
from . import glass
from . import theme as T
from . import widgets as W

NAV = [
    ("home", "Главная", "home"),
    ("zapret", "Zapret", "shield"),
    ("tg", "TG WS Proxy", "send"),
    ("updates", "Обновления", "sync"),
    ("settings", "Настройки", "settings"),
    ("log", "Журнал", "list"),
]


class App(ctk.CTk):
    STATUS_INTERVAL = 3.0

    def __init__(self, core: Core, start_minimized: bool = False):
        self.core = core
        W.apply_theme(T.make_palette(core.settings.data["appearance"]))
        super().__init__()
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
        self.apply_glass()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._drain_queue)
        self.after(500, self._check_show_flag)

        threading.Thread(target=self._status_loop, daemon=True, name="status").start()
        threading.Thread(target=self._startup, daemon=True, name="startup").start()

        self._init_tray()
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
        self.configure(fg_color=P.window_bg)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.nav = ctk.CTkFrame(self, width=212, corner_radius=18, fg_color=P.surface)
        self.nav.grid(row=0, column=0, sticky="nsw", padx=(14, 0), pady=14)
        self.nav.grid_propagate(False)
        self._logo = T.to_ctk(T.gradient_text(APP_NAME, 22, P.accents))
        ctk.CTkLabel(self.nav, text="", image=self._logo).pack(padx=20, pady=(22, 0), anchor="w")
        ctk.CTkLabel(self.nav, text="Zapret · TG WS Proxy", text_color=P.muted,
                     font=ctk.CTkFont(size=12)).pack(padx=20, pady=(0, 20), anchor="w")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=14, pady=14)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        classes = {"home": HomePage, "zapret": ZapretPage, "tg": TgPage, "updates": UpdatesPage,
                   "settings": SettingsPage, "log": LogPage}
        self.nav_items = {}
        for key, label, icon in NAV:
            self.pages[key] = (label, classes[key](self.content, self))
            item = W.NavItem(self.nav, label, icon, command=lambda k=key: self.show_page(k))
            item.pack(padx=14, pady=2)
            self.nav_items[key] = item

        ctk.CTkLabel(self.nav, text=f"v{__version__}", text_color=P.muted,
                     font=ctk.CTkFont(size=11)).pack(side="bottom", pady=14)
        self.show_page(self.current_page)

    def rebuild(self) -> None:
        """Пересоздание интерфейса после смены оформления."""
        W.apply_theme(T.make_palette(self.core.settings.data["appearance"]))
        for child in (self.nav, self.content):
            child.destroy()
        self.pages = {}
        self._build()
        self.apply_glass()
        self._apply_status(self.zapret_status, self.tg_status)
        if self.last_infos:
            self.pages["updates"][1].on_infos(self.last_infos)
            self.pages["home"][1].on_infos(self.last_infos)

    def apply_glass(self) -> str:
        a = self.core.settings.data["appearance"]
        P = W.P
        glass.set_dark_titlebar(self, P.mode == "dark")
        ok, desc = glass.apply(self, bool(a.get("glass")), P.glass_key, P.bg,
                               int(a.get("opacity", 80)), bool(a.get("blur", True)), int(a.get("blur_level", 70)))
        return desc

    def show_page(self, key: str) -> None:
        self.current_page = key
        for k, (_, page) in self.pages.items():
            if k == key:
                page.grid(row=0, column=0, sticky="nsew")
                if hasattr(page, "on_show"):
                    page.on_show()
            else:
                page.grid_forget()
            self.nav_items[k].set_active(k == key)

    def page_frame(self, parent) -> ctk.CTkScrollableFrame:
        """Прокручиваемая страница; её фон — цвет окна (в режиме стекла он прозрачный)."""
        P = W.P
        return ctk.CTkScrollableFrame(parent, fg_color=P.window_bg, scrollbar_button_color=P.border,
                                      scrollbar_button_hover_color=P.muted)

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
                    log.error("Ошибка в интерфейсе:\n%s", traceback.format_exc())
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
            page.on_infos(infos)
            self.pages["home"][1].on_infos(infos)
            self.pages["zapret"][1].reload()
            pending = [i for i in infos if i.available]
            if pending and background and self.tray is not None:
                self.tray.notify("Доступны обновления: " + ", ".join(f"{i.title} {i.latest}" for i in pending))
        self.run_task(work, done)

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
        if self.tray is not None:
            self.tray.stop()
        self.destroy()
