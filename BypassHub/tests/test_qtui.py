"""Окно Qt собирается без ошибок: все страницы, смена темы, диалоги (без экрана, offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    from PySide6.QtWidgets import QApplication

    from bypasshub.core import Core
    from bypasshub.paths import Paths
    from bypasshub.qtui import app as appmod
    from bypasshub.qtui import style as S

    paths = Paths(tmp_path_factory.mktemp("qt") / "data")
    paths.ensure()
    core = Core(paths)
    core.settings.set("vpn", "profiles", [
        {"id": f"vless:{i}", "kind": "vless", "name": f"Server {i}",
         "data": f"vless://11111111-1111-1111-1111-111111111111@1.2.3.{i}:443?security=reality&sni=a.com"
                 f"&pbk=AAAA&type=tcp#S{i}", "sub": "https://example.com/sub"} for i in range(1, 4)])
    core.settings.set("vpn", "subscriptions", ["https://example.com/sub"])
    mp = pytest.MonkeyPatch()
    mp.setattr(appmod.App, "_startup", lambda self: None)  # без загрузок и сети
    mp.setattr(appmod.App, "_status_loop", lambda self: None)
    mp.setattr(appmod.App, "_init_tray", lambda self: None)
    mp.setattr(appmod.App, "_check_show_flag", lambda self: None)
    qa = QApplication.instance() or QApplication([])
    qa.setFont(S.base_font())
    w = appmod.App(core)
    w.hotkeys.stop()
    yield qa, w
    mp.undo()
    w.close()
    w.deleteLater()
    qa.processEvents()


def test_all_pages(window):
    qa, w = window
    for key in ("home", "zapret", "tg", "vpn", "updates", "settings", "log", "home"):
        w.show_page(key)
        qa.processEvents()
        w.grab()


def test_theme_switch_rebuilds(window):
    qa, w = window
    s = w.core.settings
    for mode, colors in (("light", ["#11998e", "#38ef7d"]), ("dark", ["#2b7fff"])):
        s.data["appearance"]["mode"] = mode
        s.data["appearance"]["colors"] = colors
        w.apply_appearance(animate=False)
        w.show_page("vpn")
        qa.processEvents()
        w.grab()


def test_dialogs(window):
    from bypasshub.qtui.dialogs import PhoneDialog, TextDialog
    qa, w = window
    for d in (PhoneDialog(w), TextDialog(w, "Команда", "winws.exe --wf-tcp=80,443")):
        d.show()
        qa.processEvents()
        d.grab()
        d.close()
