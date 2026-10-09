"""Глобальные горячие клавиши (Windows RegisterHotKey).

Работают в любой программе, даже когда окно BypassHub свёрнуто в трей.
Сочетания задаются строкой вида "Ctrl+Alt+Z".
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable, Dict, List, Optional, Tuple

from .log import log

IS_WINDOWS = sys.platform == "win32"

ACTIONS: Dict[str, str] = {
    "toggle_zapret": "Включить / выключить zapret",
    "toggle_tg": "Включить / выключить TG WS Proxy",
    "show_window": "Показать окно BypassHub",
    "check_services": "Проверить доступность Discord / YouTube / Telegram",
}
DEFAULT_BINDINGS: Dict[str, str] = {
    "toggle_zapret": "Ctrl+Alt+Z",
    "toggle_tg": "Ctrl+Alt+T",
    "show_window": "Ctrl+Alt+B",
    "check_services": "",
}

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
MOD_ORDER = ("Ctrl", "Alt", "Shift", "Win")
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

_SPECIAL_KEYS = {
    "SPACE": 0x20, "HOME": 0x24, "END": 0x23, "INSERT": 0x2D, "DELETE": 0x2E,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "PAUSE": 0x13,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
}


def key_to_vk(key: str) -> Optional[int]:
    k = key.strip().upper()
    if len(k) == 1 and ("A" <= k <= "Z" or "0" <= k <= "9"):
        return ord(k)
    if k.startswith("F") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x70 + int(k[1:]) - 1
    if k.startswith("NUM") and k[3:].isdigit() and len(k) == 4:
        return 0x60 + int(k[3:])
    return _SPECIAL_KEYS.get(k)


def parse(combo: str) -> Optional[Tuple[int, int]]:
    """"Ctrl+Alt+Z" → (модификаторы, код клавиши). None — если строка неверная
    или в ней нет ни одного модификатора (иначе клавиша перестанет печататься)."""
    if not combo:
        return None
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        m = MODIFIERS.get(p.lower())
        if m is None:
            return None
        mods |= m
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None  # одна лишь Shift + буква мешала бы печатать
    vk = key_to_vk(parts[-1])
    if vk is None:
        return None
    return mods, vk


def normalize(combo: str) -> str:
    """Каноническая запись: "alt+ctrl+z" → "Ctrl+Alt+Z"."""
    parsed = parse(combo)
    if not parsed:
        return ""
    mods, _ = parsed
    key = combo.split("+")[-1].strip()
    key = key.upper() if len(key) <= 3 else key.capitalize()
    names = [m for m in MOD_ORDER if mods & MODIFIERS[m.lower()]]
    return "+".join(names + [key])


# Tk: имя клавиши события → наше имя клавиши
_TK_KEYS = {"space": "Space", "Home": "Home", "End": "End", "Insert": "Insert", "Delete": "Delete",
            "Prior": "PageUp", "Next": "PageDown", "Pause": "Pause", "Up": "Up", "Down": "Down",
            "Left": "Left", "Right": "Right"}


def combo_from_tk(state: int, keysym: str, keycode: int) -> Optional[str]:
    """Сочетание из события <KeyPress> Tk. Буквы берутся по коду клавиши, чтобы
    русская раскладка давала те же сочетания, что и английская."""
    mods = []
    if state & 0x4:
        mods.append("Ctrl")
    # Alt: 0x20000 в Windows (там 0x8 — это NumLock), Mod1 = 0x8 в X11
    if state & 0x20000 or (not IS_WINDOWS and state & 0x8):
        mods.append("Alt")
    if state & 0x1:
        mods.append("Shift")
    if keysym in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Win_L", "Win_R"):
        return None
    if IS_WINDOWS and (0x41 <= keycode <= 0x5A or 0x30 <= keycode <= 0x39):
        key = chr(keycode)
    elif IS_WINDOWS and 0x70 <= keycode <= 0x87:
        key = f"F{keycode - 0x6F}"
    elif len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
        key = keysym.upper()
    elif keysym.startswith("F") and keysym[1:].isdigit():
        key = keysym
    else:
        key = _TK_KEYS.get(keysym)
    if not key:
        return None
    return normalize("+".join(mods + [key])) or None


class HotkeyManager:
    """Регистрирует сочетания в отдельном потоке со своим циклом сообщений."""

    def __init__(self, on_action: Callable[[str], None]):
        self.on_action = on_action
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.failed: List[str] = []

    def apply(self, enabled: bool, bindings: Dict[str, str]) -> List[str]:
        """Перерегистрирует сочетания; возвращает те, что заняты другими программами."""
        self.stop()
        self.failed = []
        if not enabled or not IS_WINDOWS:
            return []
        items = [(action, combo) for action, combo in bindings.items() if action in ACTIONS and parse(combo)]
        if not items:
            return []
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, args=(items,), daemon=True, name="hotkeys")
        self._thread.start()
        self._ready.wait(3)
        return list(self.failed)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None
        self._thread_id = 0

    def _loop(self, items) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        ids: Dict[int, str] = {}
        for i, (action, combo) in enumerate(items, start=1):
            mods, vk = parse(combo)
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = action
            else:
                self.failed.append(combo)
                log.warning("Горячая клавиша %s занята другой программой", combo)
        if ids:
            log.info("Горячие клавиши: %s", ", ".join(c for a, c in items if c not in self.failed))
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    try:
                        self.on_action(ids[msg.wParam])
                    except Exception as exc:
                        log.debug("hotkey action: %r", exc)
        finally:
            for i in ids:
                user32.UnregisterHotKey(None, i)
