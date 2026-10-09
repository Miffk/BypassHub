"""Иконка в системном трее (pystray). Все действия передаются в поток GUI."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageDraw

from . import APP_NAME
from .log import log
from .paths import resource_path

if TYPE_CHECKING:
    from .gui.app import App


def _base_icon() -> Image.Image:
    try:
        return Image.open(str(resource_path("assets/icon.ico"))).convert("RGBA").resize((64, 64))
    except Exception:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([2, 2, 62, 62], fill=(43, 127, 255, 255))
        return img


def make_icon(zapret_on: bool, tg_on: bool) -> Image.Image:
    img = _base_icon().copy()
    d = ImageDraw.Draw(img)
    if zapret_on or tg_on:
        color = (61, 220, 132, 255) if (zapret_on and tg_on) else (246, 195, 67, 255)
    else:
        color = (150, 150, 150, 255)
    d.ellipse([40, 40, 63, 63], fill=color, outline=(255, 255, 255, 255), width=3)
    return img


class Tray:
    def __init__(self, app: "App"):
        import pystray  # импорт здесь: без графической среды pystray падает при импорте
        self._pystray = pystray
        self.app = app
        self._state = (False, False)
        self.icon = pystray.Icon(APP_NAME, make_icon(False, False), APP_NAME, menu=self._menu())
        self._thread: Optional[threading.Thread] = None

    def _menu(self):
        ps = self._pystray
        app = self.app
        return ps.Menu(
            ps.MenuItem("Открыть BypassHub", lambda: app.call_ui(app.show_window), default=True),
            ps.Menu.SEPARATOR,
            ps.MenuItem("Zapret", lambda: app.call_ui(lambda: app.pages["home"][1].toggle_zapret_from_tray()),
                        checked=lambda _: self._state[0]),
            ps.MenuItem("TG WS Proxy", lambda: app.call_ui(lambda: app.pages["home"][1].toggle_tg_from_tray()),
                        checked=lambda _: self._state[1]),
            ps.MenuItem("Открыть прокси в Telegram",
                        lambda: app.call_ui(lambda: app.pages["home"][1].open_in_telegram())),
            ps.Menu.SEPARATOR,
            ps.MenuItem("Проверить обновления", lambda: app.call_ui(lambda: app.run_updates())),
            ps.MenuItem("Выход", lambda: app.call_ui(app.quit_app)),
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self.icon.run, daemon=True, name="tray")
        self._thread.start()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def update(self, zapret_on: bool, tg_on: bool) -> None:
        state = (bool(zapret_on), bool(tg_on))
        if state == self._state:
            return
        self._state = state
        try:
            self.icon.icon = make_icon(*state)
            parts = [f"Zapret: {'вкл' if state[0] else 'выкл'}", f"TG Proxy: {'вкл' if state[1] else 'выкл'}"]
            self.icon.title = f"{APP_NAME}\n" + "\n".join(parts)
            self.icon.update_menu()
        except Exception as exc:
            log.debug("tray update: %r", exc)

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text, APP_NAME)
        except Exception as exc:
            log.debug("tray notify: %r", exc)
