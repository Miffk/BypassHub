"""Проверка плавности окна на Windows (запускается в GitHub Actions, workflow ui-check).

Открывает окно BypassHub (Qt) с тестовыми данными (без сети и без изменений в системе), листает
страницы, переключает вкладки, печатает время одного шага прокрутки и сохраняет снимки окна.
Аргументы: вариант (метка в выводе) и папка для снимков.
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

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from bypasshub.core import Core  # noqa: E402
from bypasshub.qtui import app as appmod  # noqa: E402
from bypasshub.qtui import style as S  # noqa: E402

core = Core(paths)
core.settings.set("vpn", "profiles", [
    {"id": f"vless:{i}", "kind": "vless", "name": f"Server {i}",
     "data": f"vless://11111111-1111-1111-1111-111111111111@1.2.3.{i}:443?security=reality&sni=a.com"
             f"&pbk=AAAA&type=tcp#S{i}", "sub": ""} for i in range(1, 9)])
core.settings.set("app", "autostart", False)
appmod.App._startup = lambda self: None          # без загрузок и восстановления состояния
appmod.App._status_loop = lambda self: None
appmod.App._init_tray = lambda self: None

QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
qa = QApplication([])
qa.setFont(S.base_font())
a = appmod.App(core)
a.setGeometry(0, 30, 1040, 720)
a.show()


def update() -> None:
    a.repaint()
    qa.processEvents()


def grab():
    """Снимок того, что реально на экране (а не перерисовка виджета заново)."""
    from PIL import Image
    img = a.screen().grabWindow(0, a.x(), a.y(), a.frameGeometry().width(), a.frameGeometry().height()).toImage()
    img = img.convertToFormat(img.Format.Format_RGB888)
    return Image.frombytes("RGB", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGB",
                           img.bytesPerLine())


def stale_check(name: str) -> None:
    """Нет ли на экране остатков прежней вкладки: снимок сразу после показа против снимка после
    полной перерисовки окна."""
    from PIL import ImageChops
    update()
    time.sleep(0.15)
    qa.processEvents()
    before = grab()
    for w in a.findChildren(QWidget):
        w.update()
    update()
    time.sleep(0.15)
    qa.processEvents()
    after = grab()
    diff = ImageChops.difference(before, after).convert("L").point(lambda v: 255 if v > 24 else 0)
    changed = sum(1 for v in diff.getdata() if v) / (diff.width * diff.height) * 100
    print(f"STALE variant={VARIANT} {name} differs={changed:.2f}%", flush=True)


def shot(name: str) -> None:
    update()
    try:
        grab().save(OUT / f"{VARIANT}-{name}.png")
    except Exception as exc:
        print("снимок не удался:", exc)


def run() -> None:
    a.raise_()
    a.activateWindow()
    update()
    time.sleep(0.5)
    shot("home")
    for rnd in (1, 2):
        for name in ("zapret", "tg", "vpn", "settings", "home"):
            t0 = time.perf_counter()
            a.show_page(name)
            update()
            print(f"RESULT variant={VARIANT} round={rnd} switch={name} {(time.perf_counter() - t0) * 1000:.1f}ms",
                  flush=True)
            if rnd == 1:
                shot(f"{name}-shown")
                stale_check(name)
    for name in ("zapret", "vpn", "settings"):
        a.show_page(name)
        update()
        page = a.pages[name][1]
        bar = page.verticalScrollBar()
        bar.setValue(0)
        update()
        times = []
        for d in [60] * 20 + [-60] * 20:
            t0 = time.perf_counter()
            bar.setValue(bar.value() + d)
            update()
            times.append((time.perf_counter() - t0) * 1000)
            if len(times) == 10:
                shot(f"{name}-scrolling")
        times.sort()
        print(f"RESULT variant={VARIANT} page={name} scroll_median={times[len(times) // 2]:.1f}ms "
              f"scroll_p90={times[int(len(times) * 0.9)]:.1f}ms widgets={len(page.findChildren(object))}",
              flush=True)
    for mode in ("light", "dark"):
        core.settings.data["appearance"]["mode"] = mode
        t0 = time.perf_counter()
        a.apply_appearance()
        update()
        print(f"RESULT variant={VARIANT} theme={mode} {(time.perf_counter() - t0) * 1000:.1f}ms", flush=True)
        a.show_page("vpn")
        shot(f"vpn-{mode}")
        stale_check(f"vpn-{mode}")
    a.show_page("home")
    shot("home-again")
    a.quit_app()


QTimer.singleShot(800, run)
qa.exec()
