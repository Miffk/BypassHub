"""Самообновление BypassHub из релизов репозитория (теги bypasshub-v*).

Работающий exe нельзя перезаписать, но можно переименовать: новый файл
кладётся на его место, старый становится BypassHub.old.exe и удаляется
следующим запуском. В Linux так же обновляется файл BypassHub-linux-x86_64.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__, github, winutil
from .log import log

REPO = "Miffk/BypassHub"
TAG_PREFIX = "bypasshub-v"
ASSET = "BypassHub-linux-x86_64" if winutil.IS_LINUX else "BypassHub.exe"


def _neighbour(exe: Path, kind: str) -> Path:
    """BypassHub.old.exe / BypassHub.new.exe; в Linux — <файл>.old / <файл>.new."""
    if winutil.IS_WINDOWS:
        return exe.with_name(f"BypassHub.{kind}.exe")
    return exe.with_name(f"{exe.name}.{kind}")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def latest_release() -> github.Release:
    """Последний релиз именно BypassHub (в репозитории могут быть и другие)."""
    with github._open(f"https://api.github.com/repos/{REPO}/releases?per_page=30", 15,
                      "application/vnd.github+json") as resp:
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


def cleanup_old(attempts: int = 40, delay: float = 0.5) -> bool:
    """Удалить BypassHub.old.exe, оставшийся после обновления.

    Старый процесс может ещё несколько секунд держать файл (загрузчик PyInstaller
    убирает за собой временную папку), поэтому пробуем несколько раз."""
    if not is_frozen():
        return True
    import time
    old = _neighbour(Path(sys.executable), "old")
    for _ in range(attempts):
        try:
            old.unlink(missing_ok=True)
            return True
        except OSError:
            time.sleep(delay)
    log.warning("Не удалось удалить %s — удалю при следующем запуске", old)
    return False


def install(release: github.Release, progress: Optional[github.ProgressCb] = None) -> None:
    """Скачивает новую версию, подменяет exe и запускает её. Вызывающий должен
    сразу после этого завершить программу."""
    if not is_frozen():
        raise RuntimeError("самообновление работает только в собранном BypassHub.exe")
    if ASSET not in release.assets:
        raise RuntimeError(f"в релизе {release.tag} нет файла {ASSET}")
    asset = release.assets[ASSET]
    exe = Path(sys.executable)
    new = _neighbour(exe, "new")
    old = _neighbour(exe, "old")
    github.download(asset.url, new, progress, asset.sha256)
    if not winutil.IS_WINDOWS:
        os.chmod(new, 0o755)
    old.unlink(missing_ok=True)
    exe.rename(old)
    try:
        new.replace(exe)
    except Exception:
        old.rename(exe)
        raise
    log.info("BypassHub обновлён до %s, перезапуск", version_of(release))
    flags = winutil.DETACHED_PROCESS | winutil.CREATE_NEW_PROCESS_GROUP if winutil.IS_WINDOWS else 0
    subprocess.Popen([str(exe), "--wait-pid", str(os.getpid())], creationflags=flags, close_fds=True,
                     env=winutil.clean_env(), start_new_session=not winutil.IS_WINDOWS)
