"""Окно на HTML: страницы собираются из настоящего Core, действия меняют настройки как кнопки окна Qt."""
import json

import pytest

from bypasshub import vpn
from bypasshub.core import Core, VpnState
from bypasshub.paths import Paths
from bypasshub.webapp.controller import NAV, WebApp

from test_vpn import REALITY


@pytest.fixture
def app(tmp_path):
    core = Core(Paths(tmp_path / "data"))
    core.vpn_add(REALITY)
    a = WebApp(core)
    a.ran = []
    a.run = lambda key, fn, done=None, busy="", title="": a.ran.append(key) or True
    return a


def test_every_page_builds(app):
    boot = app.boot()
    assert [n[0] for n in boot["nav"]] == [n[0] for n in NAV] and boot["theme"]["mode"] in ("dark", "light")
    for key, *_ in NAV:
        res = app.poll(key)
        json.dumps(res, ensure_ascii=False)  # всё уходит на страницу как JSON
        if key == "home":
            assert res["home"]["vpn"]["status"] and len(res["home"]["avail"]) == 3
            assert res["home"]["servers"] and res["home"]["speed"]["down"]
        else:
            assert res["page"]["title"] != "Ошибка" and res["page"]["blocks"], key
            assert res["rev"]


def test_page_rev_changes_with_settings(app):
    before = app.poll("vpn")["rev"]
    assert app.poll("vpn")["rev"] == before            # ничего не менялось — страница не перерисовывается
    app.act("vpn", "ru_direct", False)
    assert app.poll("vpn")["rev"] != before


def test_quick_toggles_and_vpn_power(app):
    app.act("home", "killswitch", True)
    assert app.core.settings.get("vpn", "killswitch")
    app.act("home", "vpn.power", True)
    assert app.ran[-1] == "vpn"                        # включение — в фоне
    app.act("home", "strict", True)
    assert app.ran[-1] == "strict"


def test_vpn_exclude_apps(app):
    app.act("vpn", "exclude.add", "Game.exe, other")
    names = app.core.settings.get("vpn", "exclude_apps")
    assert "game.exe" in names
    app.act("vpn", "exclude", {"value": "game.exe", "on": False})
    assert "game.exe" not in app.core.settings.get("vpn", "exclude_apps")
    assert "game.exe" in app.core.settings.get("vpn", "exclude_custom")
    app.act("vpn", "exclude.remove", "game.exe")
    assert "game.exe" not in app.core.settings.get("vpn", "exclude_custom")


def test_vpn_port_validation(app):
    assert "error" in app.act("vpn", "port.apply", "80")
    app.act("vpn", "port.apply", "3080")
    assert app.core.settings.get("vpn", "proxy_port") == 3080


def test_rename_server_prompt(app):
    pid = app.core.vpn_profiles()[0].id
    res = app.act("vpn", "rename", pid)
    assert res["prompt"] and res["then"] == "rename.do"
    app.act("vpn", "rename.do", {"arg": pid, "text": "Мой сервер"})
    assert app.core.vpn_profiles()[0].name == "Мой сервер"


def test_settings_theme_and_hotkeys(app):
    app.act("settings", "theme.mode", "Светлая")
    assert app.boot()["theme"]["mode"] == "light"
    app.act("settings", "theme.preset", "Мята")
    assert app.core.settings.data["appearance"]["colors"] == ["#11998e", "#38ef7d"]
    app.act("settings", "theme.color", {"index": 0, "color": "#ff0000"})
    assert app.core.settings.data["appearance"]["colors"][0] == "#ff0000"
    app.act("settings", "theme.tint", 30)
    assert app.boot()["theme"]["tint"] == 30
    assert "error" in app.act("settings", "hotkey.set", {"id": "toggle_vpn", "combo": "Shift+V"})
    app.act("settings", "hotkey.set", {"id": "toggle_vpn", "combo": "ctrl+alt+k"})
    assert app.core.settings.data["hotkeys"]["bindings"]["toggle_vpn"] == "Ctrl+Alt+K"
    res = app.act("settings", "hotkey.set", {"id": "toggle_tg", "combo": "Ctrl+Alt+K"})
    assert "уже назначено" in res["error"]


def test_tg_form_save_validates(app):
    res = app.act("tg", "save", {"host": "127.0.0.1", "port": "abc", "secret": "x"})
    assert "error" in res
    app.act("tg", "save", {"cfproxy_user_domain_enabled": True, "cfproxy_user_domain": ""})
    assert "Свой домен" in app.act("tg", "save", {"cfproxy_user_domain_enabled": True})["error"]


def test_zapret_game_filter_validation(app):
    assert "error" in app.act("zapret", "game.save", {"game.mode": "TCP и UDP", "game.tcp": "abc", "game.udp": "1"})


def test_home_reflects_running_vpn(app):
    app.vs = VpnState(running=True, kind="vless", mode="tun", singbox=vpn.VpnStatus(running=True),
                      selected=app.core.vpn_profiles()[0].id, current=app.core.vpn_profiles()[0].id, killswitch=True)
    st = app.poll("home")["home"]["vpn"]
    assert st["on"] and st["status"] == "Подключён" and "kill switch" in st["chips"]


def test_sheet_actions_and_close(app):
    from bypasshub.webapp.pages.sheets import TextSheet
    copied = []
    app.clipboard_set = copied.append
    app.sheet = TextSheet(app, "Команда", "winws.exe --wf-tcp=80")
    assert app.poll("zapret")["page"]["sheet"]["title"] == "Команда"
    app.act("zapret", "sheet.copy")
    assert copied == ["winws.exe --wf-tcp=80"]
    app.act("zapret", "sheet.close")
    assert app.sheet is None and "sheet" not in app.poll("zapret")["page"]


def test_unknown_action_is_error(app):
    assert "error" in app.act("home", "no.such.action")
