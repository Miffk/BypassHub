"""Проверка плавности окна на Windows (запускается в GitHub Actions, workflow ui-check).

Открывает окно BypassHub с тестовыми данными (без сети и без изменений в системе), листает каждую
страницу, переключает вкладки, печатает время одного шага прокрутки и сохраняет снимки окна.
Вариант (аргумент): base — как есть; flat — без градиентного фона; nothumb — без полосы прокрутки.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VARIANT = sys.argv[1] if len(sys.argv) > 1 else "base"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "ui-check")
OUT.mkdir(parents=True, exist_ok=True)

from bypasshub.log import setup_logging  # noqa: E402
from bypasshub.paths import Paths  # noqa: E402

paths = Paths(Path(tempfile.mkdtemp()) / "data")
paths.ensure()
setup_logging(paths.log_file)

from bypasshub.core import Core  # noqa: E402
from bypasshub.gui import app as appmod  # noqa: E402
from bypasshub.gui import surface  # noqa: E402

core = Core(paths)
core.settings.set("vpn", "profiles", [
    {"id": f"vless:{i}", "kind": "vless", "name": f"Server {i}",
     "data": f"vless://11111111-1111-1111-1111-111111111111@1.2.3.{i}:443?security=reality&sni=a.com"
             f"&pbk=AAAA&type=tcp#S{i}", "sub": ""} for i in range(1, 9)])
core.settings.set("app", "autostart", False)
appmod.App._startup = lambda self: None          # без загрузок и восстановления состояния
appmod.App._status_loop = lambda self: None
appmod.App._init_tray = lambda self: None

if VARIANT == "flat":
    def flat(self, width, height):
        for item in self._bands:
            self.delete(item)
        self._bands = []
        self.configure(bg=self.color_at(0))
    surface._GradientMixin._draw_background = flat
elif VARIANT == "nothumb":
    surface.GradientPage._place_thumb = lambda self: None

a = appmod.App(core)
a.geometry("1040x720+0+0")


def shot(name: str) -> None:
    from PIL import ImageGrab
    a.update()
    x, y, w, h = a.winfo_rootx(), a.winfo_rooty(), a.winfo_width(), a.winfo_height()
    try:
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(OUT / f"{VARIANT}-{name}.png")
    except Exception as exc:
        print("снимок не удался:", exc)


def run() -> None:
    a.lift()
    a.attributes("-topmost", True)
    a.update()
    time.sleep(0.5)
    shot("home")
    for name in ("zapret", "tg", "vpn", "settings"):
        t0 = time.perf_counter()
        a.show_page(name)
        a.update()
        switch = (time.perf_counter() - t0) * 1000
        shot(f"{name}-shown")
        page = a.pages[name][1]
        page._view.yview_moveto(0)
        a.update()
        times = []
        for d in [60] * 20 + [-60] * 20:
            t0 = time.perf_counter()
            page._view.yview_scroll(d, "units")
            a.update()
            times.append((time.perf_counter() - t0) * 1000)
            if len(times) == 10:
                shot(f"{name}-scrolling")
        times.sort()
        print(f"RESULT variant={VARIANT} page={name} switch={switch:.1f}ms "
              f"scroll_median={times[len(times) // 2]:.1f}ms scroll_p90={times[int(len(times) * 0.9)]:.1f}ms "
              f"widgets={_count(page)}", flush=True)
    a.show_page("home")
    shot("home-again")
    a.destroy()


def _count(w) -> int:
    return 1 + sum(_count(c) for c in w.winfo_children())


a.after(800, run)
a.mainloop()
