"""Эффект «стекла»: полупрозрачный фон окна с размытием того, что под ним.

Фон окна рисуется специальным цветом-ключом, который делается прозрачным
(-transparentcolor), а под окном включается композиция Windows (Acrylic /
Aero blur) с подкраской цветом темы. Непрозрачность подкраски задаётся
ползунком. Карточки остаются непрозрачными, поэтому текст читается всегда.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Tuple

from ..log import log

IS_WINDOWS = sys.platform == "win32"

ACCENT_DISABLED = 0
ACCENT_ENABLE_TRANSPARENTGRADIENT = 2
ACCENT_ENABLE_BLURBEHIND = 3          # мягкое размытие (Aero)
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4   # сильное размытие с шумом (Acrylic)
WCA_ACCENT_POLICY = 19


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_uint), ("AccentFlags", ctypes.c_uint),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_uint)]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t)]


def blur_level_name(level: int) -> str:
    if level <= 0:
        return "без размытия"
    return "мягкое (Aero)" if level < 50 else "сильное (Acrylic)"


def _hwnd(window) -> int:
    window.update_idletasks()
    return ctypes.windll.user32.GetParent(window.winfo_id())


def _abgr(color: str, alpha: int) -> int:
    c = color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (alpha << 24) | (b << 16) | (g << 8) | r


def _set_accent(hwnd: int, state: int, tint: int) -> bool:
    accent = ACCENT_POLICY(state, 2 if state else 0, tint, 0)
    data = WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.pointer(accent), ctypes.sizeof(accent))
    try:
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    except Exception as exc:  # Windows 7 и старее
        log.debug("SetWindowCompositionAttribute: %r", exc)
        return False


def set_dark_titlebar(window, dark: bool) -> None:
    if not IS_WINDOWS:
        return
    try:
        hwnd = _hwnd(window)
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (новые и старые сборки)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception as exc:
        log.debug("dark titlebar: %r", exc)


def apply(window, enabled: bool, key_color: str, tint_color: str, opacity: int,
          blur: bool, blur_level: int) -> Tuple[bool, str]:
    """Включает/выключает стекло. Возвращает (успех, описание режима)."""
    if not IS_WINDOWS:
        # на других ОС — только общая прозрачность окна
        try:
            window.attributes("-alpha", 1.0 if not enabled else max(0.3, opacity / 100))
        except Exception:
            pass
        return False, "эффект стекла доступен только в Windows 10/11"
    try:
        hwnd = _hwnd(window)
        if not enabled:
            window.attributes("-transparentcolor", "")
            _set_accent(hwnd, ACCENT_DISABLED, 0)
            return True, "выключено"
        window.attributes("-transparentcolor", key_color)
        alpha = int(255 * max(0, min(100, opacity)) / 100)
        if blur and blur_level > 0:
            state = ACCENT_ENABLE_BLURBEHIND if blur_level < 50 else ACCENT_ENABLE_ACRYLICBLURBEHIND
            # Acrylic с полностью непрозрачной подкраской выглядит как сплошной фон
            alpha = min(alpha, 250)
        else:
            state = ACCENT_ENABLE_TRANSPARENTGRADIENT
        ok = _set_accent(hwnd, state, _abgr(tint_color, alpha))
        return ok, blur_level_name(blur_level if blur else 0)
    except Exception as exc:
        log.warning("Эффект стекла недоступен: %r", exc)
        return False, str(exc)
