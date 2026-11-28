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
    from bypasshub import github
    mp.setattr(Core, "zapret_versions", lambda self: [github.Release("x/y", "1.9.1", ""),
                                                      github.Release("x/y", "1.9.0", "")])
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
    for key in ("home", "zapret", "tg", "vpn", "updates", "changelog", "settings", "log", "home"):
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


def test_add_several_conf_files(window, tmp_path, monkeypatch):
    """Можно выбрать сразу несколько .conf (по файлу на страну из AmneziaVPN): каждый — свой сервер
    с названием по имени файла."""
    import time
    from PySide6.QtWidgets import QFileDialog
    from test_vpn import CONF
    qa, w = window
    paths = []
    for i, country in enumerate(("Germany", "Netherlands")):
        p = tmp_path / f"{country}.conf"
        p.write_text(CONF.replace("203.0.113.5", f"203.0.113.{20 + i}"), encoding="utf-8")
        paths.append(str(p))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **kw: (paths, "")))
    shown = []
    monkeypatch.setattr(w, "info", lambda text, *a, **kw: shown.append(text))
    monkeypatch.setattr(w, "error", lambda text, *a, **kw: shown.append("ERROR " + text))
    w.pages["vpn"][1].add_from_file()
    deadline = time.time() + 10
    while not shown and time.time() < deadline:
        qa.processEvents()
        time.sleep(0.02)
    assert shown and not shown[0].startswith("ERROR"), shown
    names = {p.name for p in w.core.vpn_profiles() if p.kind == "awg"}
    assert {"Germany", "Netherlands"} <= names


def test_exclude_apps_become_checkboxes(window, monkeypatch):
    """Своя программа в «мимо VPN» появляется в списке с галочкой; снятая галочка — остаётся в списке
    неотмеченной; ✕ — убирает из списка."""
    qa, w = window
    page = w.pages["vpn"][1]
    s = w.core.settings
    monkeypatch.setattr(w.pages["home"][1], "apply_vpn", lambda *a, **kw: None)
    page.exclude_entry.setText("DeadByDaylight.exe, Repo.exe")
    page._exclude_add()
    qa.processEvents()
    assert page.exclude_entry.text() == ""
    for name in ("deadbydaylight.exe", "repo.exe"):
        assert page.exclude_checks[name][0].isChecked()
    assert {"deadbydaylight.exe", "repo.exe"} <= set(s.get("vpn", "exclude_apps"))
    page.exclude_checks["repo.exe"][0].setChecked(False)
    qa.processEvents()
    assert "repo.exe" not in s.get("vpn", "exclude_apps")
    assert "repo.exe" in page.exclude_checks and not page.exclude_checks["repo.exe"][0].isChecked()
    page._exclude_remove("repo.exe")
    qa.processEvents()
    assert "repo.exe" not in page.exclude_checks and "repo.exe" not in s.get("vpn", "exclude_custom")


def test_zapret_version_list(window):
    qa, w = window
    import time
    page = w.pages["zapret"][1]
    page._load_versions()
    deadline = time.time() + 5
    while page.version_box.count() < 2 and time.time() < deadline:
        qa.processEvents()
        time.sleep(0.02)
    assert [page.version_box.itemText(i) for i in range(page.version_box.count())] == \
        ["1.9.1 — последняя", "1.9.0"]


def test_butterflies(window):
    """Бабочки в меню: все кадры читаются из assets, пролёт идёт до конца и освобождает кадры."""
    from bypasshub.qtui.butterflies import load_frames
    qa, w = window
    canvas, duration, frames = load_frames(0.5)
    assert len(frames) == 48 and canvas == (225, 440) and 50 <= duration <= 200
    assert sum(f is not None for f in frames) > 40
    area = w.butterflies
    area._start((canvas, 1, frames))
    w.grab()
    for _ in range(len(frames) + 2):
        area._next()
    assert not area.busy and area.frames == []
    s = w.core.settings
    s.data["appearance"]["butterflies"] = False
    w._butterflies_at = 0
    w.play_butterflies()
    assert not area.busy
    s.data["appearance"]["butterflies"] = True


def test_changelog_dot_after_update(window):
    """После обновления у «Что нового» точка; открыли вкладку — точки нет."""
    from bypasshub import __version__
    qa, w = window
    s = w.core.settings
    s.set("app", "seen_changelog", "1.0.0")
    w._changelog_mark()
    assert "•" in w.nav_items["changelog"].text_label.text()
    w.show_page("changelog")
    assert "•" not in w.nav_items["changelog"].text_label.text()
    assert s.get("app", "seen_changelog") == __version__
    w.show_page("home")
