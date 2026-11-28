"""Буфер обмена без окна Qt: Windows — через WinAPI, Linux — wl-copy/xclip/xsel."""
from __future__ import annotations

import shutil
import subprocess

from .. import winutil
from ..log import log

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _win():
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u.SetClipboardData.restype = wintypes.HANDLE
    u.GetClipboardData.argtypes = [wintypes.UINT]
    u.GetClipboardData.restype = wintypes.HANDLE
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    return ctypes, u, k


def set_text(text: str) -> None:
    try:
        if winutil.IS_WINDOWS:
            ctypes, u, k = _win()
            data = (text or "").encode("utf-16-le") + b"\0\0"
            if not u.OpenClipboard(None):
                raise OSError("буфер обмена занят")
            try:
                u.EmptyClipboard()
                h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
                ptr = k.GlobalLock(h)
                ctypes.memmove(ptr, data, len(data))
                k.GlobalUnlock(h)
                u.SetClipboardData(CF_UNICODETEXT, h)
            finally:
                u.CloseClipboard()
            return
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, input=(text or "").encode("utf-8"), timeout=5, check=False)
                return
    except Exception as exc:
        log.warning("Буфер обмена: %r", exc)


def get_text() -> str:
    try:
        if winutil.IS_WINDOWS:
            ctypes, u, k = _win()
            if not u.OpenClipboard(None):
                return ""
            try:
                h = u.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                ptr = k.GlobalLock(h)
                try:
                    return ctypes.wstring_at(ptr) if ptr else ""
                finally:
                    k.GlobalUnlock(h)
            finally:
                u.CloseClipboard()
        for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"],
                    ["xsel", "--clipboard", "--output"]):
            if shutil.which(cmd[0]):
                res = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
                return res.stdout.decode("utf-8", "replace")
    except Exception as exc:
        log.warning("Буфер обмена: %r", exc)
    return ""
