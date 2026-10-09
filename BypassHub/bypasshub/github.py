"""Получение последних релизов с GitHub и скачивание файлов."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import APP_NAME, __version__
from .log import log

USER_AGENT = f"{APP_NAME}/{__version__}"
ProgressCb = Callable[[int, int], None]


@dataclass
class Asset:
    name: str
    url: str
    sha256: Optional[str] = None


@dataclass
class Release:
    repo: str
    tag: str
    html_url: str
    assets: Dict[str, Asset] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")

    def asset(self, name: str) -> Asset:
        """Ассет по имени; если API был недоступен — ссылка строится по шаблону GitHub."""
        if name in self.assets:
            return self.assets[name]
        return Asset(name, f"https://github.com/{self.repo}/releases/download/{self.tag}/{name}")


def parse_version(text: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums)


def is_newer(latest: str, installed: str) -> bool:
    if not installed:
        return True
    a, b = parse_version(latest), parse_version(installed)
    if a and b:
        return a > b
    return latest.strip().lstrip("vV") != installed.strip().lstrip("vV")


def _request(url: str, accept: str = "*/*") -> Request:
    return Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                 "Cache-Control": "no-cache"})


def _release_from_api(repo: str) -> Release:
    req = _request(f"https://api.github.com/repos/{repo}/releases/latest",
                   "application/vnd.github+json")
    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = (data.get("tag_name") or "").strip()
    if not tag:
        raise ValueError("в ответе GitHub нет tag_name")
    assets: Dict[str, Asset] = {}
    for a in data.get("assets") or []:
        name, url = a.get("name"), a.get("browser_download_url")
        if not name or not url:
            continue
        digest = a.get("digest") or ""
        sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
        assets[name] = Asset(name, url, sha)
    return Release(repo, tag, data.get("html_url") or f"https://github.com/{repo}/releases/latest",
                   assets)


def _release_from_redirect(repo: str) -> Release:
    """Без API (например, при лимите запросов): /releases/latest редиректит на /tag/<tag>."""
    with urlopen(_request(f"https://github.com/{repo}/releases/latest"), timeout=15) as resp:
        final = resp.geturl()
    m = re.search(r"/releases/tag/([^/?#]+)", final)
    if not m:
        raise ValueError(f"не удалось определить тег по адресу {final}")
    tag = m.group(1)
    return Release(repo, tag, final)


def latest_release(repo: str) -> Release:
    try:
        return _release_from_api(repo)
    except HTTPError as exc:
        log.debug("GitHub API %s: HTTP %s, пробую без API", repo, exc.code)
    except Exception as exc:
        log.debug("GitHub API %s: %r, пробую без API", repo, exc)
    try:
        return _release_from_redirect(repo)
    except HTTPError as exc:
        raise RuntimeError(f"GitHub ответил HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"нет соединения с GitHub ({exc.reason})") from None


def fetch_text(url: str, timeout: float = 15) -> str:
    sep = "&" if "?" in url else "?"
    with urlopen(_request(f"{url}{sep}t={int(time.time())}"), timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def download(url: str, dest: Path, progress: Optional[ProgressCb] = None,
             sha256: Optional[str] = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    log.info("Скачивание %s", url)
    with urlopen(_request(url), timeout=30) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if total and done != total:
        tmp.unlink(missing_ok=True)
        raise IOError(f"файл скачан не полностью ({done} из {total} байт)")
    if sha256 and h.hexdigest().lower() != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise IOError("контрольная сумма SHA-256 не совпала с указанной на GitHub")
    tmp.replace(dest)
    return dest
