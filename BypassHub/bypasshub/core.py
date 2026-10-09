"""Логика программы без GUI: включение/выключение, обновления, восстановление состояния."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import github, tgproxy, zapret
from .log import log
from .paths import Paths
from .settings import Settings
from .tgproxy import TgProxyManager
from .zapret import ZapretManager

ProgressCb = Callable[[str, int, int], None]  # (компонент, сделано, всего)


@dataclass
class UpdateInfo:
    component: str          # "zapret" | "tg"
    title: str
    installed: str
    latest: str
    release: Optional[github.Release]
    error: str = ""

    @property
    def available(self) -> bool:
        return self.release is not None and github.is_newer(self.latest, self.installed)


class Core:
    def __init__(self, paths: Paths):
        self.paths = paths
        paths.ensure()
        self.settings = Settings(paths.settings)
        self.zapret = ZapretManager(paths.zapret, paths.winws_log)
        self.tg = TgProxyManager(paths.tgproxy)
        # Все операции, меняющие состояние, выполняются по одной
        self.lock = threading.RLock()
        self.last_updates: List[UpdateInfo] = []

    # ------------------------------------------------------------ zapret
    @property
    def strategy(self) -> str:
        return self.settings.get("zapret", "strategy")

    @property
    def zapret_mode(self) -> str:
        return self.settings.get("zapret", "mode")

    def zapret_enable(self) -> None:
        with self.lock:
            self.settings.set("zapret", "enabled", True)
            self.zapret.start(self.strategy, self.zapret_mode)

    def zapret_disable(self) -> None:
        with self.lock:
            self.settings.set("zapret", "enabled", False)
            self.zapret.stop()

    def zapret_apply(self) -> bool:
        """Перезапускает обход, если он включён, чтобы применились новые настройки."""
        with self.lock:
            st = self.zapret.status()
            if st.running or st.service_installed:
                log.info("Перезапуск zapret для применения настроек")
                self.zapret.start(self.strategy, self.zapret_mode)
                return True
            return False

    # ------------------------------------------------------------ tg-ws-proxy
    def tg_enable(self) -> None:
        with self.lock:
            self.settings.set("tg", "enabled", True)
            self.tg.start()

    def tg_disable(self) -> None:
        with self.lock:
            self.settings.set("tg", "enabled", False)
            self.tg.stop()

    def tg_apply(self) -> bool:
        with self.lock:
            if self.tg.status().running:
                self.tg.restart()
                return True
            return False

    # ------------------------------------------------------------ обновления
    def check_zapret(self) -> UpdateInfo:
        installed = self.zapret.local_version() if self.zapret.is_installed() else ""
        try:
            rel = github.latest_release(zapret.REPO)
        except Exception as exc:
            try:  # последний запасной вариант — version.txt в репозитории
                ver = github.fetch_text(zapret.VERSION_URL).strip()
                rel = github.Release(zapret.REPO, ver, f"https://github.com/{zapret.REPO}/releases/tag/{ver}")
            except Exception:
                return UpdateInfo("zapret", "Zapret", installed, "", None, str(exc))
        return UpdateInfo("zapret", "Zapret", installed, rel.version, rel)

    def check_tg(self) -> UpdateInfo:
        installed = self.tg.local_version() if self.tg.is_installed() else ""
        try:
            rel = github.latest_release(tgproxy.REPO)
        except Exception as exc:
            return UpdateInfo("tg", "TG WS Proxy", installed, "", None, str(exc))
        return UpdateInfo("tg", "TG WS Proxy", installed, rel.version, rel)

    def check_all(self) -> List[UpdateInfo]:
        infos = [self.check_zapret(), self.check_tg()]
        self.settings.set("updates", "last_check", int(time.time()))
        for i in infos:
            if i.error:
                log.warning("%s: не удалось проверить обновления: %s", i.title, i.error)
            elif i.available:
                log.info("%s: доступна версия %s (установлена %s)", i.title, i.latest, i.installed or "—")
            else:
                log.info("%s: установлена последняя версия %s", i.title, i.installed)
        self.last_updates = infos
        return infos

    def install(self, info: UpdateInfo, progress: Optional[ProgressCb] = None) -> None:
        if info.release is None:
            raise RuntimeError(info.error or "нет данных о релизе")
        cb = (lambda done, total: progress(info.component, done, total)) if progress else None
        with self.lock:
            if info.component == "zapret":
                self._install_zapret(info.release, cb)
            else:
                asset = tgproxy.choose_asset(self.settings.get("tg", "asset"))
                self.tg.install_release(info.release, asset, self.paths.downloads, cb)
                self.settings.set("tg", "installed_version", info.release.version)
                if self.settings.get("tg", "enabled") and not self.tg.status().running:
                    self.tg.start()
            info.installed = info.release.version

    def _install_zapret(self, rel: github.Release, cb) -> None:
        asset = rel.asset(f"zapret-discord-youtube-{rel.tag}.zip")
        zip_path = self.paths.downloads / asset.name
        github.download(asset.url, zip_path, cb, asset.sha256)
        try:
            strategy = self.zapret.install_release(zip_path, self.strategy, self.zapret_mode)
        finally:
            zip_path.unlink(missing_ok=True)
        self.settings.set("zapret", "strategy", strategy, save=False)
        self.settings.set("zapret", "installed_version", rel.version)
        log.info("Zapret %s установлен", rel.version)
        if self.settings.get("zapret", "enabled") and not self.zapret.status().running:
            self.zapret.start(self.strategy, self.zapret_mode)

    def update_cycle(self, force_install: bool = False,
                     progress: Optional[ProgressCb] = None) -> List[UpdateInfo]:
        """Проверка + (при включённой автоустановке или если компонент ещё не скачан) установка."""
        infos = self.check_all()
        auto = self.settings.get("updates", "auto_install") or force_install
        for info in infos:
            if not info.available:
                continue
            if auto or not info.installed:
                try:
                    log.info("%s: установка версии %s", info.title, info.latest)
                    self.install(info, progress)
                except Exception as exc:
                    info.error = str(exc)
                    log.error("%s: ошибка обновления: %s", info.title, exc)
        return infos

    # ------------------------------------------------------------ запуск / выход
    def cleanup(self) -> None:
        """Удаляет остатки прошлых обновлений, чтобы не занимать место."""
        import shutil
        for name in ("zapret_old", "zapret_new", "zapret_extract"):
            shutil.rmtree(self.paths.root / name, ignore_errors=True)
        for f in self.paths.downloads.glob("*"):
            try:
                shutil.rmtree(f) if f.is_dir() else f.unlink()
            except OSError:
                pass
        old = self.tg.exe.with_suffix(".old")
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass

    def restore_state(self) -> None:
        if not self.settings.get("app", "restore_state"):
            return
        if self.settings.get("zapret", "enabled"):
            st = self.zapret.status()
            if not st.running:
                try:
                    self.zapret.start(self.strategy, self.zapret_mode)
                except Exception as exc:
                    log.error("Не удалось включить zapret: %s", exc)
        if self.settings.get("tg", "enabled"):
            try:
                self.tg.start()
            except Exception as exc:
                log.error("Не удалось запустить tg-ws-proxy: %s", exc)

    def shutdown(self) -> None:
        if not self.settings.get("app", "stop_on_exit"):
            return
        with self.lock:
            try:
                st = self.zapret.status()
                if st.running and not st.service_installed:
                    self.zapret.kill_winws()
            except Exception as exc:
                log.warning("Остановка zapret при выходе: %r", exc)
            try:
                self.tg.stop()
            except Exception as exc:
                log.warning("Остановка tg-ws-proxy при выходе: %r", exc)
