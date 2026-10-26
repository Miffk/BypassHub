"""HTTPS-запрос с запасным DNS.

DNS провайдера часто не знает (или не отдаёт) адреса сервисов подписок VPN: urlopen падает с
«getaddrinfo failed», хотя Happ и другие клиенты подписку скачивают — они узнают адрес через
DNS по HTTPS. Здесь так же: если система адрес не нашла, он запрашивается у Cloudflare и Google
(по IP, без DNS), а соединение идёт на этот адрес с настоящим именем сайта (SNI и проверка
сертификата — как обычно).
"""
from __future__ import annotations

import http.client
import json
import socket
from typing import Dict, List
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

from . import tls
from .log import log

# DNS по HTTPS по IP-адресу: сертификаты Cloudflare и Google выданы и на эти IP
DOH_SERVERS = ["https://1.1.1.1/dns-query?name={name}&type=A", "https://8.8.8.8/resolve?name={name}&type=A"]


class Response:
    """То, что нужно от ответа: тело, заголовки и итоговый адрес."""

    def __init__(self, body: bytes, headers, url: str):
        self.body, self.headers, self.url = body, headers, url


def doh_resolve(name: str, timeout: float = 8) -> List[str]:
    """IPv4-адреса имени через DNS по HTTPS (пусто — не нашлось)."""
    for template in DOH_SERVERS:
        req = Request(template.format(name=quote(name)), headers={"Accept": "application/dns-json"})
        try:
            with urlopen(req, timeout=timeout, context=tls.context()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log.debug("DoH %s: %r", template.split("/")[2], exc)
            continue
        ips = [a.get("data") for a in data.get("Answer") or [] if a.get("type") == 1 and a.get("data")]
        if ips:
            return ips
    return []


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS к заданному IP, но с именем сайта в SNI и при проверке сертификата."""

    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=tls.context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _is_dns_failure(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, socket.gaierror)


def _fetch_pinned(url: str, headers: Dict[str, str], timeout: float, limit: int, redirects: int = 3) -> Response:
    for _ in range(redirects + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise URLError(f"запасной DNS — только для https: {url}")
        ips = doh_resolve(parts.hostname)
        if not ips:
            raise URLError(f"адрес {parts.hostname} не найден ни системным DNS, ни через DNS по HTTPS")
        conn = _PinnedHTTPSConnection(parts.hostname, ips[0], parts.port or 443, timeout)
        try:
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            conn.request("GET", path, headers=dict(headers, Host=parts.netloc))
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urljoin(url, resp.getheader("Location"))
                continue
            if resp.status >= 400:
                raise URLError(f"HTTP {resp.status}")
            return Response(resp.read(limit), resp.headers, url)
        finally:
            conn.close()
    raise URLError("слишком много перенаправлений")


def fetch(url: str, headers: Dict[str, str], timeout: float = 20, limit: int = 4 * 1024 * 1024) -> Response:
    """GET: обычным путём, а если система не нашла адрес сайта — через DNS по HTTPS."""
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout, context=tls.context()) as resp:
            return Response(resp.read(limit), resp.headers, resp.geturl())
    except URLError as exc:
        if not _is_dns_failure(exc):
            raise
        log.info("Адрес %s не найден системным DNS — пробую DNS по HTTPS", urlsplit(url).hostname)
        return _fetch_pinned(url, headers, timeout, limit)
