import pytest

from bypasshub import hotkeys
from bypasshub.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, combo_from_tk, normalize, parse


@pytest.mark.parametrize("combo,expected", [
    ("Ctrl+Alt+Z", (MOD_CONTROL | MOD_ALT, ord("Z"))),
    ("ctrl+shift+5", (MOD_CONTROL | MOD_SHIFT, ord("5"))),
    ("Alt+F12", (MOD_ALT, 0x7B)),
    ("Ctrl+Alt+PageUp", (MOD_CONTROL | MOD_ALT, 0x21)),
    ("Z", None),             # без модификатора
    ("Shift+Z", None),       # только Shift мешал бы печатать
    ("Ctrl+Hyper+Z", None),  # неизвестный модификатор
    ("Ctrl+Alt+ЯЯ", None),
    ("", None),
])
def test_parse(combo, expected):
    assert parse(combo) == expected


def test_normalize():
    assert normalize("alt+ctrl+z") == "Ctrl+Alt+Z"
    assert normalize("shift+alt+f5") == "Alt+Shift+F5"
    assert normalize("Ctrl+pageup") == "Ctrl+Pageup"
    assert normalize("Z") == ""


def test_defaults_are_valid():
    for action, combo in hotkeys.DEFAULT_BINDINGS.items():
        assert action in hotkeys.ACTIONS
        assert combo == "" or parse(combo)


def test_combo_from_tk_x11_states():
    # X11: Control = 0x4, Mod1 (Alt) = 0x8, Shift = 0x1
    if hotkeys.IS_WINDOWS:
        pytest.skip("состояния Tk в Windows другие")
    assert combo_from_tk(0x4 | 0x8, "z", 0) == "Ctrl+Alt+Z"
    assert combo_from_tk(0x4 | 0x1, "F7", 0) == "Ctrl+Shift+F7"
    assert combo_from_tk(0x4, "Control_L", 0) is None
    assert combo_from_tk(0x0, "z", 0) is None


def test_manager_noop_when_disabled():
    m = hotkeys.HotkeyManager(lambda a: None)
    assert m.apply(False, hotkeys.DEFAULT_BINDINGS) == []
    m.stop()


def test_combo_from_tk_windows_states(monkeypatch):
    # Windows: Ctrl = 0x4, Alt = 0x20000, 0x8 — NumLock; keycode — виртуальный код клавиши,
    # поэтому русская раскладка (keysym «Cyrillic_ya») даёт ту же букву Z
    monkeypatch.setattr(hotkeys, "IS_WINDOWS", True)
    assert combo_from_tk(0x4 | 0x20000 | 0x8, "Cyrillic_ya", 0x5A) == "Ctrl+Alt+Z"
    assert combo_from_tk(0x4 | 0x8, "z", 0x5A) == "Ctrl+Z"  # NumLock не считается за Alt
    assert combo_from_tk(0x20000, "F5", 0x74) == "Alt+F5"
