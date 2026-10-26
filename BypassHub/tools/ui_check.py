"""Проверка плавности окна на Windows (запускается в GitHub Actions, workflow ui-check).

Открывает окно BypassHub с тестовыми данными (без сети и без изменений в системе), листает каждую
страницу, переключает вкладки, печатает время одного шага прокрутки и сохраняет снимки окна.
Вариант (аргумент): base — как есть; flat — без градиентного фона; bandN — полосы фона по N px.
Окно со стилем WS_EX_COMPOSITED (как в 1.6.8) на Windows зависает намертво — так его и не проверить.
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
elif VARIANT.startswith("band"):
    surface.BAND_PX = int(VARIANT[4:])

a = appmod.App(core)
a.geometry("1040x720+0+0")

def grab():
    from PIL import ImageGrab
    x, y, w, h = a.winfo_rootx(), a.winfo_rooty(), a.winfo_width(), a.winfo_height()
    return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGB")


def stale_check(name: str) -> None:
    """Нет ли на экране остатков прежней вкладки: снимок сразу после показа против снимка после
    полной перерисовки окна. У 1.6.8 (WS_EX_COMPOSITED) старая страница оставалась поверх новой."""
    from PIL import ImageChops
    a.update()
    time.sleep(0.15)
    before = grab()
    if sys.platform == "win32":
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        hwnd = user32.GetParent(a.winfo_id())
        user32.RedrawWindow(ctypes.c_void_p(hwnd), None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)
    a.update()
    time.sleep(0.15)
    after = grab()
    diff = ImageChops.difference(before, after).convert("L").point(lambda v: 255 if v > 24 else 0)
    changed = sum(1 for v in diff.getdata() if v) / (diff.width * diff.height) * 100
    print(f"STALE variant={VARIANT} {name} differs={changed:.2f}%", flush=True)


def shot(name: str) -> None:
    from PIL import ImageGrab
    a.update()
    x, y, w, h = a.winfo_rootx(), a.winfo_rooty(), a.winfo_width(), a.winfo_height()
    try:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        img.save(OUT / f"{VARIANT}-{name}.png")
    except Exception as exc:
        print("снимок не удался:", exc)
        return



def run() -> None:
    a.lift()
    a.attributes("-topmost", True)
    a.update()
    time.sleep(0.5)
    shot("home")
    for rnd in (1, 2):
        for name in ("zapret", "tg", "vpn", "settings", "home"):
            t0 = time.perf_counter()
            a.show_page(name)
            a.update()
            print(f"RESULT variant={VARIANT} round={rnd} switch={name} {(time.perf_counter() - t0) * 1000:.1f}ms",
                  flush=True)
            if rnd == 1:
                shot(f"{name}-shown")
                stale_check(name)
    for name in ("zapret", "vpn", "settings"):
        a.show_page(name)
        a.update()
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
        print(f"RESULT variant={VARIANT} page={name} scroll_median={times[len(times) // 2]:.1f}ms "
              f"scroll_p90={times[int(len(times) * 0.9)]:.1f}ms widgets={_count(page)}", flush=True)
    if VARIANT == "base":
        import cProfile
        import pstats
        a.show_page("home")
        a.update()
        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            a.show_page("vpn")
            a.update()
            a.show_page("home")
            a.update()
        prof.disable()
        pstats.Stats(prof).sort_stats("tottime").print_stats(18)
        pstats.Stats(prof).sort_stats("cumulative").print_stats(25)
    a.show_page("home")
    shot("home-again")
    a.destroy()


def _count(w) -> int:
    return 1 + sum(_count(c) for c in w.winfo_children())


def wait_prewarm() -> None:
    # вкладки готовятся в фоне после запуска — меряем, как у пользователя через пару секунд
    if getattr(a, "prewarmed", True):
        a.after(300, run)
    else:
        a.after(100, wait_prewarm)


a.after(800, wait_prewarm)
a.mainloop()
