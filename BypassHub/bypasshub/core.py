"""Логика программы без GUI: включение/выключение, обновления, восстановление состояния."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import awg, github, selfupdate, tgproxy, vpn, zapret
from .applearn import AppLearner
from .awg import AwgManager
from .log import log
from .paths import Paths
from .settings import Settings
from .tgproxy import TgProxyManager
from .vpn import Profile, VpnManager
from .zapret import ZapretManager

ProgressCb = Callable[[str, int, int], None]  # (компонент, сделано, всего)


@dataclass
class UpdateInfo:
    component: str          # "zapret" | "tg" | "singbox" | "awg" | "self"
    title: str
    installed: str
    latest: str
    release: Optional[github.Release]
    error: str = ""

    @property
    def available(self) -> bool:
        return self.release is not None and github.is_newer(self.latest, self.installed)


@dataclass
class VpnState:
    running: bool = False
    kind: str = ""          # vless | awg
    mode: str = ""          # tun | proxy
    singbox: Optional[vpn.VpnStatus] = None
    awg: Optional[awg.AwgStatus] = None
    selected: str = ""      # VLESS: «auto» или id выбранного сервера
    current: str = ""       # VLESS: id сервера, через который идёт трафик сейчас
    delays: Dict[str, int] = field(default_factory=dict)  # задержки по проверкам sing-box, мс


class Core:
    def __init__(self, paths: Paths):
        self.paths = paths
        paths.ensure()
        self.settings = Settings(paths.settings)
        self.zapret = ZapretManager(paths.zapret, paths.winws_log)
        self.tg = TgProxyManager(paths.tgproxy)
        self.singbox = VpnManager(paths.singbox, paths.vpn_log)
        self.awg = AwgManager(paths.amneziawg)
        # Все операции, меняющие состояние, выполняются по одной
        self.lock = threading.RLock()
        self.last_updates: List[UpdateInfo] = []
        self.learner = AppLearner(self.zapret.lists / "ipset-exclude-user.txt")
        self.restarting_for_update = False

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

    # ------------------------------------------------------------ VPN
    def vpn_profiles(self) -> List[Profile]:
        items = [Profile.from_dict(d) for d in self.settings.get("vpn", "profiles") or []
                 if isinstance(d, dict)]
        return [p for p in items if p is not None]

    def _save_profiles(self, profiles: List[Profile]) -> None:
        self.settings.set("vpn", "profiles", [p.to_dict() for p in profiles])

    def vpn_vless(self) -> List[Profile]:
        """Серверы VLESS с правильными ключами — из них собирается группа и «авто»."""
        out = []
        for p in self.vpn_profiles():
            if p.kind == "vless":
                try:
                    vpn.parse_vless(p.data)
                    out.append(p)
                except ValueError:
                    pass
        return out

    def vpn_is_auto(self) -> bool:
        """Выбран режим «Авто — лучший сервер» (имеет смысл от двух серверов VLESS)."""
        return self.settings.get("vpn", "active") == vpn.AUTO_TAG and len(self.vpn_vless()) >= 2

    def vpn_active(self) -> Optional[Profile]:
        """Выбранный сервер; None — серверов нет или выбран режим «авто»."""
        profiles = self.vpn_profiles()
        if self.vpn_is_auto():
            return None
        active = self.settings.get("vpn", "active")
        fallback = next((p for p in profiles if p.kind == "vless"), profiles[0] if profiles else None)
        return next((p for p in profiles if p.id == active), fallback)

    def _hwid(self) -> str:
        """Код этого устройства для подписок (см. vpn.device_headers): создаётся один раз."""
        hwid = self.settings.get("vpn", "hwid")
        if not hwid:
            import uuid
            hwid = uuid.uuid4().hex[:16]
            self.settings.set("vpn", "hwid", hwid)
        return hwid

    def vpn_select(self, profile_id: str) -> None:
        self.settings.set("vpn", "active", profile_id)

    def vpn_add(self, text: str) -> Tuple[List[Profile], List[str]]:
        """Добавляет серверы из вставленного текста; подписки скачиваются сразу.
        Возвращает (новые серверы, предупреждения)."""
        parsed = vpn.parse_input(text)
        problems = list(parsed.problems)
        found = list(parsed.profiles)
        subs = list(self.settings.get("vpn", "subscriptions") or [])
        for url in parsed.subscriptions:
            try:
                keys, extra, hours = vpn.fetch_subscription_info(url, hwid=self._hwid())
            except Exception as exc:
                problems.append(f"подписка {url}: не удалось скачать ({exc})")
                continue
            self._mark_sub(url, hours)
            if not keys:
                problems.append(f"в подписке {url} нет ключей VLESS")
            found += [vpn.vless_profile(k, url) for k in keys]
            problems += extra
            if url not in subs:
                subs.append(url)
        self.settings.set("vpn", "subscriptions", subs, save=False)
        profiles = self.vpn_profiles()
        known = {p.id for p in profiles}
        added: List[Profile] = []
        for p in found:
            if p.id not in known:
                known.add(p.id)
                added.append(p)
        profiles += added
        self._save_profiles(profiles)
        if added and not any(p.id == self.settings.get("vpn", "active") for p in profiles):
            self.vpn_select(added[0].id)
        return added, problems

    def vpn_remove(self, profile_id: str) -> None:
        self._save_profiles([p for p in self.vpn_profiles() if p.id != profile_id])

    def vpn_rename(self, profile_id: str, name: str) -> None:
        profiles = self.vpn_profiles()
        for p in profiles:
            if p.id == profile_id and name.strip():
                p.name = name.strip()
        self._save_profiles(profiles)

    def vpn_refresh_subscriptions(self, only_due: bool = False) -> Tuple[int, List[str]]:
        """Обновляет серверы из подписок: старые ключи подписки заменяются новыми.
        only_due — только те, которым пора (раз в несколько часов, см. vpn.SUB_REFRESH_HOURS)."""
        problems: List[str] = []
        profiles = self.vpn_profiles()
        active = self.vpn_active()
        total = 0
        for url in self.settings.get("vpn", "subscriptions") or []:
            if only_due and not self._sub_due(url):
                continue
            try:
                keys, extra, hours = vpn.fetch_subscription_info(url, hwid=self._hwid())
            except Exception as exc:
                problems.append(f"{url}: {exc}")
                continue
            self._mark_sub(url, hours)
            problems += extra
            if not keys:  # пустой ответ (сбой сервиса) — старые серверы не трогаем
                problems.append(f"{url}: в ответе нет ключей VLESS, оставлены прежние серверы")
                continue
            fresh = [vpn.vless_profile(k, url) for k in keys]
            names = {p.id: p.name for p in profiles if p.sub == url}
            for p in fresh:  # сохранить переименования
                p.name = names.get(p.id, p.name)
            profiles = [p for p in profiles if p.sub != url] + fresh
            total += len(fresh)
            # у выбранного сервера сменился ключ — остаёмся на сервере с тем же названием
            if active is not None and active.sub == url and all(p.id != active.id for p in fresh):
                same = next((p for p in fresh if p.name == active.name), None)
                if same is not None and not self.vpn_is_auto():
                    self.vpn_select(same.id)
        self._save_profiles(profiles)
        return total, problems

    def _sub_due(self, url: str) -> bool:
        meta = (self.settings.get("vpn", "subs_meta") or {}).get(url) or {}
        hours = float(meta.get("hours") or vpn.SUB_REFRESH_HOURS)
        return time.time() - float(meta.get("checked") or 0) >= hours * 3600

    def _mark_sub(self, url: str, hours: float) -> None:
        meta = dict(self.settings.get("vpn", "subs_meta") or {})
        meta[url] = {"checked": time.time(), "hours": hours}
        self.settings.set("vpn", "subs_meta", meta, save=False)

    def vpn_sub_checked(self, url: str) -> float:
        """Когда подписка обновлялась последний раз (0 — неизвестно)."""
        return float(((self.settings.get("vpn", "subs_meta") or {}).get(url) or {}).get("checked") or 0)

    def vpn_remove_subscription(self, url: str) -> None:
        subs = [u for u in self.settings.get("vpn", "subscriptions") or [] if u != url]
        self.settings.set("vpn", "subscriptions", subs, save=False)
        meta = {u: m for u, m in (self.settings.get("vpn", "subs_meta") or {}).items() if u in subs}
        self.settings.set("vpn", "subs_meta", meta, save=False)
        self._save_profiles([p for p in self.vpn_profiles() if p.sub != url])

    def vpn_running(self) -> bool:
        return self.singbox.status().running or self.awg.status().running

    def vpn_state(self) -> "VpnState":
        sb, aw = self.singbox.status(), self.awg.status()
        if aw.running:
            kind, mode = "awg", (sb.mode if sb.running else "tun")
        elif sb.running:
            kind, mode = "vless", sb.mode
        else:
            kind, mode = "", ""
        state = VpnState(running=sb.running or aw.running, kind=kind, mode=mode, singbox=sb, awg=aw)
        if kind == "vless" and self.singbox.has_group():
            try:
                state.selected, state.current, state.delays = self.singbox.group_state()
            except Exception as exc:
                log.debug("VPN: состояние группы: %r", exc)
        return state

    def _ensure_engines(self, need_singbox: bool, need_awg: bool, progress: Optional[ProgressCb]) -> None:
        if need_awg and not self.awg.is_installed():
            self.install(self.check_awg(), progress)
        if need_singbox and not self.singbox.is_installed():
            self.install(self.check_singbox(), progress)

    def _vpn_start(self, progress: Optional[ProgressCb] = None) -> None:
        profile = self.vpn_active()
        auto = self.vpn_is_auto()
        if profile is None and not auto:
            raise RuntimeError("Сначала добавьте сервер: вкладка VPN → вставьте ключ vless://… или vpn://…")
        s = self.settings
        mode = s.get("vpn", "mode")
        ru_direct = bool(s.get("vpn", "ru_direct"))
        port = int(s.get("vpn", "proxy_port"))
        exclude = vpn.clean_exe_names(s.get("vpn", "exclude_apps") or []) if mode == "tun" else []
        if profile is not None and profile.kind == "awg":
            conf = awg.parse_conf(profile.data, profile.name)
            # раздельное туннелирование, режим прокси и программы мимо VPN — через sing-box поверх туннеля
            split = ru_direct or mode == "proxy" or bool(exclude)
            self._ensure_engines(split, True, progress)
            self.singbox.stop()
            if not split:
                self.awg.start(conf, bool(s.get("vpn", "lan_access")))
                return
            bypass = awg.endpoint_ips(conf)
            self.awg.start(conf, split=True)
            try:
                self.singbox.start(vpn.awg_outbound(awg.TUNNEL_NAME), profile.name, mode, ru_direct, port, bypass,
                                   exclude)
            except Exception:
                self.awg.stop()
                raise
        else:
            # все серверы VLESS — одной группой: можно переключаться на лету и выбрать «авто»
            self._ensure_engines(True, False, progress)
            self.awg.stop()
            servers = [vpn.outbound(vpn.parse_vless(p.data), tag=p.id) for p in self.vpn_vless()]
            selected = vpn.AUTO_TAG if auto or profile is None else profile.id
            name = "Авто — лучший сервер" if selected == vpn.AUTO_TAG else profile.name
            self.singbox.start(vpn.server_group(servers, selected), name, mode, ru_direct, port,
                               exclude_apps=exclude)

    def vpn_enable(self, progress: Optional[ProgressCb] = None) -> None:
        with self.lock:
            self.settings.set("vpn", "enabled", True)
            try:
                self._vpn_start(progress)
            except Exception:
                self.settings.set("vpn", "enabled", False)
                raise

    def vpn_disable(self) -> None:
        with self.lock:
            self.settings.set("vpn", "enabled", False)
            self.singbox.stop()
            self.awg.stop()

    def vpn_switch(self) -> bool:
        """Включённый VPN — на выбранный сервер. Между серверами VLESS (и «авто») —
        на лету, без переподключения; иначе — перезапуск. True — если VPN был включён."""
        with self.lock:
            if not self.vpn_running():
                return False
            target = vpn.AUTO_TAG if self.vpn_is_auto() else (self.vpn_active().id if self.vpn_active() else "")
            target_vless = target == vpn.AUTO_TAG or any(p.id == target for p in self.vpn_vless())
            if target_vless and not self.awg.status().running and self.singbox.has_group():
                try:
                    if target not in self.singbox.group_tags() + [vpn.AUTO_TAG]:
                        raise RuntimeError("сервера нет в запущенном конфиге")
                    self.singbox.select_server(target)
                    log.info("VPN: переключение на %s", target)
                    return True
                except Exception as exc:  # например, сервер добавлен после запуска — перезапуск
                    log.debug("VPN: переключение через API не удалось: %r", exc)
            self._vpn_start()
            return True

    def vpn_ping(self) -> Tuple[Dict[str, Optional[float]], bool]:
        """Задержка до серверов VLESS: {id: мс или None}. Если VPN включён — реальная, через
        сами серверы (второе значение True); иначе — время подключения к серверу."""
        vless = self.vpn_vless()
        result: Dict[str, Optional[float]] = {}
        real = False
        if self.singbox.status().running and self.singbox.has_group() and not self.awg.status().running:
            try:
                delays = self.singbox.test_delays()
                in_group = set(self.singbox.group_tags())
                for p in vless:
                    if p.id in in_group:
                        result[p.id] = float(delays[p.id]) if p.id in delays else None
                real = True
            except Exception as exc:
                log.debug("VPN: проверка задержек через sing-box: %r", exc)
        rest = [p for p in vless if p.id not in result]
        if rest:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(8) as ex:
                for p, ms in zip(rest, ex.map(lambda p: vpn.ping(vpn.parse_vless(p.data)), rest)):
                    result[p.id] = ms
        return result, real

    def vpn_apply(self) -> bool:
        """Перезапуск VPN с новыми настройками или другим сервером, если он включён."""
        with self.lock:
            if self.vpn_running():
                self._vpn_start()
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

    def check_self(self) -> UpdateInfo:
        from . import __version__
        try:
            rel = selfupdate.latest_release()
        except Exception as exc:
            return UpdateInfo("self", "BypassHub", __version__, "", None, str(exc))
        return UpdateInfo("self", "BypassHub", __version__, selfupdate.version_of(rel), rel)

    def check_singbox(self) -> UpdateInfo:
        installed = self.singbox.local_version() if self.singbox.is_installed() else ""
        try:
            rel = vpn.latest_release()
        except Exception as exc:
            return UpdateInfo("singbox", "VPN: sing-box", installed, "", None, str(exc))
        return UpdateInfo("singbox", "VPN: sing-box", installed, rel.version, rel)

    def check_awg(self) -> UpdateInfo:
        installed = self.awg.local_version() if self.awg.is_installed() else ""
        try:
            rel = awg.latest_release()
        except Exception as exc:
            return UpdateInfo("awg", "VPN: AmneziaWG", installed, "", None, str(exc))
        return UpdateInfo("awg", "VPN: AmneziaWG", installed, rel.version, rel)

    def check_all(self) -> List[UpdateInfo]:
        infos = [self.check_zapret(), self.check_tg()]
        # движки VPN скачиваются при первом включении, дальше — обновляются как всё остальное
        if self.singbox.is_installed():
            infos.append(self.check_singbox())
            self.singbox.update_rule_sets()  # базы раздельного туннелирования, если устарели
        if self.awg.is_installed():
            infos.append(self.check_awg())
        if self.settings.get("updates", "self_update"):
            infos.append(self.check_self())
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
            elif info.component == "self":
                selfupdate.install(info.release, cb)
                self.restarting_for_update = True
            elif info.component == "singbox":
                self.singbox.install_release(info.release, self.paths.downloads, cb)
            elif info.component == "awg":
                self.awg.install_release(info.release, self.paths.downloads, cb)
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
        # сначала компоненты, сам BypassHub — последним (после него программа перезапустится)
        for info in sorted(infos, key=lambda i: i.component == "self"):
            if not info.available:
                continue
            if info.component == "self" and not selfupdate.is_frozen():
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
        # старый exe после самообновления удаляется в фоне — он может ещё быть занят
        threading.Thread(target=selfupdate.cleanup_old, daemon=True, name="cleanup-old").start()
        try:  # прокси Windows остался указывать на выключенный VPN (сбой, выключение ПК)
            self.singbox.recover()
        except Exception as exc:
            log.warning("VPN: восстановление настроек прокси: %r", exc)
        old = self.tg.exe.with_suffix(".old")
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass

    def update_learner(self) -> None:
        if self.settings.get("zapret", "learn_apps") and self.zapret.is_installed():
            self.learner.start(self.zapret.app_exclusions()[0])
        else:
            self.learner.stop()

    def restore_state(self) -> None:
        self.update_learner()
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
        if self.settings.get("vpn", "enabled") and not self.vpn_running():
            try:
                self._vpn_start()
            except Exception as exc:
                log.error("Не удалось включить VPN: %s", exc)

    def shutdown(self) -> None:
        self.learner.stop()
        if self.restarting_for_update or not self.settings.get("app", "stop_on_exit"):
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
            try:
                self.singbox.stop()
                self.awg.stop()
            except Exception as exc:
                log.warning("Остановка VPN при выходе: %r", exc)
