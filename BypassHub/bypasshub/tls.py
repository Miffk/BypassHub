"""Проверка сертификатов HTTPS: корневые сертификаты Windows плюс встроенный набор certifi.

Python доверяет только сертификатам, которые уже лежат в хранилище Windows, а Windows докачивает
корневые сертификаты лишь по запросу (браузера, например). Поэтому на части компьютеров GitHub
в браузере открывался, а BypassHub получал «unable to get local issuer certificate». Набор certifi
закрывает этот случай, а хранилище Windows оставляет доверие к сертификатам, которые добавили
антивирус или администратор (проверка HTTPS антивирусом).
"""
from __future__ import annotations

import ssl
from functools import lru_cache
from typing import Optional

from .log import log


def _add_bundled(ctx: ssl.SSLContext) -> None:
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception as exc:  # без набора — как раньше, только хранилище Windows
        log.debug("Встроенные корневые сертификаты не загружены: %r", exc)


@lru_cache(maxsize=None)
def context(version: Optional[ssl.TLSVersion] = None) -> ssl.SSLContext:
    """Общий контекст для HTTPS; version — только эта версия TLS (для проверки доступности сайтов)."""
    ctx = ssl.create_default_context()
    _add_bundled(ctx)
    if version is not None:
        ctx.minimum_version = version
        ctx.maximum_version = version
    return ctx
