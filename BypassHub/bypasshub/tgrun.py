"""Linux: tg-ws-proxy из официальных исходников релиза, без окна и значка в трее.

Готовая сборка TgWsProxy_linux_amd64 — приложение для трея со своими GTK и OpenSSL; на части
систем оно запускается, но порт так и не открывает. Поэтому в Linux BypassHub скачивает
исходники того же релиза и запускает сам прокси (proxy.tg_ws_proxy:main — консольная команда
tg-ws-proxy из pyproject.toml) своим Python: `BypassHub --run-tg-proxy <папка исходников> -- <аргументы>`.

Импорты ниже — зависимости прокси: так PyInstaller включает их в сборку BypassHub.
"""
from __future__ import annotations

import argparse  # noqa: F401
import asyncio  # noqa: F401
import base64  # noqa: F401
import collections  # noqa: F401
import ctypes  # noqa: F401
import dataclasses  # noqa: F401
import functools  # noqa: F401
import hashlib  # noqa: F401
import hmac  # noqa: F401
import http.client  # noqa: F401
import logging.handlers  # noqa: F401
import random  # noqa: F401
import signal  # noqa: F401
import ssl  # noqa: F401
import string  # noqa: F401
import struct  # noqa: F401
import sys
import urllib.parse  # noqa: F401
import urllib.request  # noqa: F401
from typing import List

try:  # в Windows-сборке этих пакетов нет — там работает TgWsProxy.exe
    import certifi  # noqa: F401
    import h2.config  # noqa: F401
    import h2.connection  # noqa: F401
    import h2.errors  # noqa: F401
    import h2.events  # noqa: F401
    import h2.exceptions  # noqa: F401
    import h2.settings  # noqa: F401
    import httpx  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: F401
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
except ImportError:  # pragma: no cover
    pass

FLAG = "--run-tg-proxy"


def run(src: str, args: List[str]) -> int:
    """Запустить прокси из папки исходников src с аргументами командной строки tg-ws-proxy."""
    sys.path.insert(0, src)
    sys.argv = ["tg-ws-proxy"] + list(args)
    from proxy.tg_ws_proxy import main  # type: ignore[import-not-found]
    main()
    return 0
