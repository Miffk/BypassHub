"""Проверка доступности сайтов: HTTPS-запрос с заданной версией TLS.

Используется индикаторами «Discord / YouTube / Telegram» и автоподбором
стратегии zapret (аналог проверок curl из utils/test zapret.ps1).
"""
from __future__ import annotations

import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlsplit

TLS12 = "TLS1.2"
TLS13 = "TLS1.3"


@dataclass
class Probe:
    url: str
    tls: str
    ok: bool
    ms: float = 0.0
    error: str = ""


def _context(tls: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    version = ssl.TLSVersion.TLSv1_3 if tls == TLS13 else ssl.TLSVersion.TLSv1_2
    ctx.minimum_version = version
    ctx.maximum_version = version
    return ctx


def probe(url: str, tls: str = TLS12, timeout: float = 5.0) -> Probe:
    """TCP + TLS + HEAD-запрос. Успех — получен любой HTTP-ответ."""
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or 443
    path = parts.path or "/"
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=min(3.0, timeout)) as raw:
            raw.settimeout(timeout)
            with _context(tls).wrap_socket(raw, server_hostname=host) as s:
                s.sendall(f"HEAD {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\n"
                          f"Connection: close\r\n\r\n".encode())
                head = s.recv(64)
        ok = head.startswith(b"HTTP/")
        return Probe(url, tls, ok, (time.monotonic() - start) * 1000, "" if ok else "нет HTTP-ответа")
    except ssl.SSLCertVerificationError:
        return Probe(url, tls, False, 0, "подмена сертификата (DNS/DPI)")
    except (socket.timeout, TimeoutError):
        return Probe(url, tls, False, 0, "таймаут")
    except socket.gaierror:
        return Probe(url, tls, False, 0, "DNS не находит адрес")
    except ConnectionResetError:
        return Probe(url, tls, False, 0, "соединение сброшено (похоже на DPI)")
    except ConnectionRefusedError:
        return Probe(url, tls, False, 0, "соединение отклонено")
    except ssl.SSLError as exc:
        return Probe(url, tls, False, 0, f"ошибка TLS ({exc.reason or exc})")
    except Exception as exc:
        return Probe(url, tls, False, 0, type(exc).__name__)


def probe_many(urls: Sequence[str], tls_versions: Sequence[str] = (TLS12, TLS13),
               timeout: float = 5.0, workers: int = 12) -> List[Probe]:
    jobs = [(u, t) for u in urls for t in tls_versions]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda job: probe(job[0], job[1], timeout), jobs))


# ---------------------------------------------------------------- индикаторы сервисов

SERVICES: Dict[str, List[str]] = {
    "Discord": ["https://discord.com", "https://gateway.discord.gg", "https://cdn.discordapp.com"],
    "YouTube": ["https://www.youtube.com", "https://i.ytimg.com", "https://redirector.googlevideo.com"],
    "Telegram": ["https://telegram.org", "https://web.telegram.org"],
}


@dataclass
class ServiceState:
    name: str
    ok: bool
    partial: bool
    ms: Optional[float]
    detail: str


def check_services(timeout: float = 6.0) -> List[ServiceState]:
    urls = [u for lst in SERVICES.values() for u in lst]
    results = {p.url: p for p in probe_many(urls, (TLS13,), timeout)}
    states = []
    for name, lst in SERVICES.items():
        probes = [results[u] for u in lst]
        good = [p for p in probes if p.ok]
        ms = sum(p.ms for p in good) / len(good) if good else None
        bad = [f"{urlsplit(p.url).hostname}: {p.error}" for p in probes if not p.ok]
        states.append(ServiceState(name, len(good) == len(probes), 0 < len(good) < len(probes), ms,
                                   "; ".join(bad)))
    return states
