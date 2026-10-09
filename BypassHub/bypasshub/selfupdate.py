"""Самообновление BypassHub из релизов репозитория (теги bypasshub-v*).

Работающий exe нельзя перезаписать, но можно переименовать: новый файл
кладётся на его место, старый становится BypassHub.old.exe и удаляется
следующим запуском.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

from . import __version__, github, winutil
from .log import log

REPO = "Miffk/games"
TAG_PREFIX = "bypasshub-v"
ASSET = "BypassHub.exe"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def latest_release() -> github.Release:
    """Последний релиз именно BypassHub (в репозитории могут быть и другие)."""
    req = github._request(f"https://api.github.com/repos/{REPO}/releases?per_page=30",
                          "application/vnd.github+json")
    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    best: Optional[github.Release] = None
    for rel in data:
        tag = rel.get("tag_name") or ""
        if not tag.startswith(TAG_PREFIX) or rel.get("draft") or rel.get("prerelease"):
            continue
        assets = {}
        for a in rel.get("assets") or []:
            digest = a.get("digest") or ""
            assets[a["name"]] = github.Asset(a["name"], a["browser_download_url"],
                                             digest.split(":", 1)[1] if digest.startswith("sha256:") else None)
        candidate = github.Release(REPO, tag, rel.get("html_url") or "", assets)
        if best is None or github.parse_version(candidate.version) > github.parse_version(best.version):
            best = candidate
    if best is None:
        raise RuntimeError("релизов BypassHub пока нет")
    return best


def version_of(release: github.Release) -> str:
    return release.tag[len(TAG_PREFIX):] if release.tag.startswith(TAG_PREFIX) else release.version


def is_newer(release: github.Release) -> bool:
    return github.is_newer(version_of(release), __version__)


def cleanup_old() -> None:
    if is_frozen():
        Path(sys.executable).with_name("BypassHub.old.exe").unlink(missing_ok=True)


def install(release: github.Release, progress: Optional[github.ProgressCb] = None) -> None:
    """Скачивает новую версию, подменяет exe и запускает её. Вызывающий должен
    сразу после этого завершить программу."""
    if not is_frozen():
        raise RuntimeError("самообновление работает только в собранном BypassHub.exe")
    if ASSET not in release.assets:
        raise RuntimeError(f"в релизе {release.tag} нет файла {ASSET}")
    asset = release.assets[ASSET]
    exe = Path(sys.executable)
    new = exe.with_name("BypassHub.new.exe")
    old = exe.with_name("BypassHub.old.exe")
    github.download(asset.url, new, progress, asset.sha256)
    old.unlink(missing_ok=True)
    exe.rename(old)
    try:
        new.replace(exe)
    except Exception:
        old.rename(exe)
        raise
    log.info("BypassHub обновлён до %s, перезапуск", version_of(release))
    flags = winutil.DETACHED_PROCESS | winutil.CREATE_NEW_PROCESS_GROUP if winutil.IS_WINDOWS else 0
    subprocess.Popen([str(exe), "--wait-pid", str(os.getpid())], creationflags=flags, close_fds=True)
