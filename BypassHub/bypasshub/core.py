"""Логика программы без GUI: включение/выключение, обновления, восстановление состояния."""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import awg, github, selfupdate, tgproxy, vpn, winutil, zapret
from .applearn import AppLearner
from .awg import AwgManager
from .killswitch import LINUX_MARK, KillSwitch, Plan
from .log import log
from .paths import Paths
from .settings import Settings
from .tgproxy import TgProxyManager
from .vpn import Profile, VpnManager
from .zapret import ZapretManager

ProgressCb = Callable[[str, int, int], None]  # (компонент, сделано, всего)
WATCH_INTERVAL = 5          # как часто проверять, что VPN работает, с
RECONNECT_MAX_DELAY = 60    # пауза между попытками переподключения растёт до этого значения, с


def _addresses(text: str) -> List[str]:
    """Address из [Interface] конфига WireGuard: «10.8.1.2/32, fd00::2/128» → список."""
    return [a.strip() for a in (text or "").split(",") if a.strip()]


@dataclass
class UpdateInfo:
    component: str          # "zapret" | "tg" | "singbox" | "awg" | "self"
    title: str
    installed: str
    latest: str
    release: Optional[github.Release]
    error: str = ""
    pinned: bool = False    # версия выбрана вручную — не обновляется

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
    killswitch: bool = False      # kill switch сейчас закрывает интернет мимо VPN
    reconnecting: bool = False    # VPN неожиданно остановился — BypassHub подключает его снова

    @property
    def active(self) -> bool:
        """VPN включён: работает или переподключается после сбоя."""
        return self.running or self.reconnecting


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
        self.killswitch = KillSwitch()
        # что запущено ради включённого VPN («singbox», «awg»); пусто — VPN не включали в этом сеансе
        self._vpn_parts: Tuple[str, ...] = ()
        self._endpoint_cache: Dict[str, List[str]] = {}  # IP серверов AmneziaWG: на случай, если DNS недоступен
        self._watch_stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_fails = 0
        self._watch_next = 0.0
        self._offline_logged = False  # в журнал уже написали, что строгий kill switch закрыл интернет

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
        state = VpnState(running=sb.running or aw.running, kind=kind, mode=mode, singbox=sb, awg=aw,
                         killswitch=self.killswitch.active)
        if self._vpn_parts and self.settings.get("vpn", "enabled") and not self._vpn_healthy(sb, aw):
            state.running, state.reconnecting = False, True
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
        ks = self.killswitch_wanted(mode)
        mark = LINUX_MARK if ks and winutil.IS_LINUX else 0
        tun = dict(tunnel_addrs=[vpn.TUN_ADDRESS], tunnel_ifaces=[vpn.INTERFACE_NAME])
        if profile is not None and profile.kind == "awg":
            conf = awg.parse_conf(profile.data, profile.name)
            # раздельное туннелирование, режим прокси и программы мимо VPN — через sing-box поверх туннеля;
            # в Linux — всегда: туннель там отдаёт SOCKS5, а не сетевой адаптер
            split = ru_direct or mode == "proxy" or bool(exclude) or winutil.IS_LINUX
            self._ensure_engines(split, True, progress)
            self.singbox.stop()
            if not split:
                # без своего kill switch клиента AmneziaWG: локальная сеть доступна, а интернет мимо
                # туннеля при включённом kill switch закрывает BypassHub
                self._killswitch_set(ks, lambda: Plan(apps=[self.awg.exe], tunnel_addrs=_addresses(conf.address),
                                                      endpoints=self._awg_endpoints(conf, required=False)))
                self.awg.start(conf, lan_access=True)
                self._vpn_parts = ("awg",)
                return
            bypass = self._awg_endpoints(conf)
            apps = [self.singbox.exe] + ([] if winutil.IS_LINUX else [self.awg.exe])
            # через адаптер AmneziaWG (Windows) — уже внутри VPN
            addrs = [vpn.TUN_ADDRESS] + ([] if winutil.IS_LINUX else _addresses(conf.address))
            self._killswitch_set(ks, lambda: Plan(apps=apps, endpoints=bypass, tunnel_addrs=addrs,
                                                  tunnel_ifaces=[vpn.INTERFACE_NAME]))
            socks = self.awg.start(conf, split=True)
            outbound = vpn.socks_outbound(socks) if winutil.IS_LINUX else vpn.awg_outbound(awg.TUNNEL_NAME)
            try:
                self.singbox.start(outbound, profile.name, mode, ru_direct, port, bypass, exclude, mark=mark)
            except Exception:
                self.awg.stop()
                raise
            self._vpn_parts = ("awg", "singbox")
        else:
            # все серверы VLESS — одной группой: можно переключаться на лету и выбрать «авто»
            self._ensure_engines(True, False, progress)
            self.awg.stop()
            servers = [vpn.outbound(vpn.parse_vless(p.data), tag=p.id) for p in self.vpn_vless()]
            selected = vpn.AUTO_TAG if auto or profile is None else profile.id
            name = "Авто — лучший сервер" if selected == vpn.AUTO_TAG else profile.name
            self._killswitch_set(ks, lambda: Plan(apps=[self.singbox.exe], **tun))
            self.singbox.start(vpn.server_group(servers, selected), name, mode, ru_direct, port,
                               exclude_apps=exclude, mark=mark)
            self._vpn_parts = ("singbox",)

    # ------------------------------------------------------------ kill switch и переподключение
    def killswitch_wanted(self, mode: Optional[str] = None) -> bool:
        """Kill switch при подключённом VPN: включён (обычный или строгий), режим «Весь трафик»
        и система его умеет."""
        mode = mode or self.settings.get("vpn", "mode")
        on = self.settings.get("vpn", "killswitch") or self.settings.get("vpn", "killswitch_strict")
        return bool(on) and mode == "tun" and self.killswitch.supported

    def killswitch_strict(self) -> bool:
        """Строгий режим: интернет закрыт и когда VPN выключен."""
        return bool(self.settings.get("vpn", "killswitch_strict")) and self.killswitch.supported

    def _offline_plan(self) -> Plan:
        """VPN выключен, строгий режим: пропускаем только BypassHub и движки VPN (чтобы VPN можно было
        включить, обновить подписку, проверить серверы), DNS и локальную сеть."""
        return Plan(apps=[Path(sys.executable), self.singbox.exe, self.awg.exe], dns=True)

    def _killswitch_idle(self) -> None:
        """VPN выключен: в строгом режиме интернет закрыт, иначе — как обычно."""
        if not self.killswitch_strict():
            self._killswitch_off()
            return
        try:
            self.killswitch.enable(self._offline_plan())
        except Exception as exc:
            log.error("Строгий kill switch не включился: %s", exc)
            return
        if not self._offline_logged:
            self._offline_logged = True
            log.info("Строгий kill switch: VPN выключен — интернет закрыт, пока вы не включите VPN")

    def set_killswitch_strict(self, on: bool) -> None:
        """Переключатель «Строгий режим». VPN выключен — сразу закрываем или открываем интернет."""
        with self.lock:
            self.settings.set("vpn", "killswitch_strict", bool(on))
            self._offline_logged = False
            if not self._vpn_parts:
                self._killswitch_idle()

    def _killswitch_set(self, on: bool, plan: Callable[[], Plan]) -> None:
        if not on:
            self.killswitch.disable()
            return
        try:
            self.killswitch.enable(plan())
        except Exception as exc:
            raise RuntimeError(f"Kill switch не включился: {exc}\n\nВыключите kill switch во вкладке VPN, "
                               "чтобы подключаться без него.") from None

    def _awg_endpoints(self, conf: awg.AwgProfile, required: bool = True) -> List[str]:
        """IP сервера AmneziaWG. Если имя сервера не узнать (при kill switch DNS бывает закрыт,
        пока VPN переподключается) — адреса с прошлого подключения."""
        try:
            ips = awg.endpoint_ips(conf)
        except RuntimeError:
            cached = self._endpoint_cache.get(conf.endpoint)
            if cached:
                log.info("AmneziaWG: адрес сервера — с прошлого подключения (%s)", ", ".join(cached))
                return list(cached)
            if required:
                raise
            return []
        self._endpoint_cache[conf.endpoint] = ips
        return ips

    def _vpn_healthy(self, sb: Optional[vpn.VpnStatus] = None, aw: Optional[awg.AwgStatus] = None) -> bool:
        """Работает всё, что запускали ради VPN."""
        parts = self._vpn_parts
        if "singbox" in parts and not (sb or self.singbox.status()).running:
            return False
        if "awg" in parts and not (aw or self.awg.status()).running:
            return False
        return True

    def start_watchdog(self) -> None:
        if self._watch_thread is None or not self._watch_thread.is_alive():
            self._watch_stop.clear()
            self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True, name="vpn-watchdog")
            self._watch_thread.start()

    def _watch_loop(self) -> None:
        while not self._watch_stop.wait(WATCH_INTERVAL):
            try:
                self.watch_once()
            except Exception as exc:
                log.debug("VPN: проверка: %r", exc)

    def watch_once(self) -> None:
        """VPN неожиданно остановился (упал движок, служба туннеля) — подключаем его снова.
        Пока подключается, kill switch (если включён) не пускает интернет мимо VPN."""
        if not self._vpn_parts or self.restarting_for_update or not self.settings.get("vpn", "enabled"):
            self._watch_fails = 0
            return
        if not self.lock.acquire(blocking=False):
            return  # VPN сейчас включают, выключают или переключают
        try:
            if not self._vpn_parts or self._vpn_healthy():
                self._watch_fails = 0
                return
            if time.monotonic() < self._watch_next:
                return
            if self._watch_fails == 0:
                log.warning("VPN неожиданно остановился — подключаю снова%s",
                            " (kill switch не пускает интернет мимо VPN)" if self.killswitch.active else "")
            try:
                self._vpn_start()
            except Exception as exc:
                self._watch_fails += 1
                delay = min(RECONNECT_MAX_DELAY, WATCH_INTERVAL * 2 ** (self._watch_fails - 1))
                self._watch_next = time.monotonic() + delay
                log.error("VPN не подключился: %s — следующая попытка через %d с", exc, delay)
            else:
                self._watch_fails = 0
                log.info("VPN снова подключён")
        finally:
            self.lock.release()

    def vpn_enable(self, progress: Optional[ProgressCb] = None) -> None:
        with self.lock:
            self.settings.set("vpn", "enabled", True)
            try:
                self._vpn_start(progress)
            except Exception:
                self.settings.set("vpn", "enabled", False)
                self._vpn_parts = ()
                self._killswitch_idle()
                raise

    def vpn_disable(self) -> None:
        with self.lock:
            self.settings.set("vpn", "enabled", False)
            self._vpn_parts = ()
            try:
                self.singbox.stop()
                self.awg.stop()
            finally:
                self._killswitch_idle()

    def _killswitch_off(self) -> None:
        """Интернет снова как обычно."""
        self._offline_logged = False
        try:
            self.killswitch.disable()
        except Exception as exc:
            log.error("Не удалось выключить kill switch: %s", exc)

    def vpn_switch(self) -> bool:
        """Включённый VPN — на выбранный сервер. Между серверами VLESS (и «авто») —
        на лету, без переподключения; иначе — перезапуск. True — если VPN был включён."""
        with self.lock:
            if not self.vpn_running():
                if self._vpn_parts and self.settings.get("vpn", "enabled"):  # переподключается — сразу на новый
                    self._vpn_start()
                    return True
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
        if not real and vless:  # VPN выключен — меряем через сами серверы отдельным sing-box
            servers = []
            for p in vless:
                try:
                    servers.append(vpn.outbound(vpn.parse_vless(p.data), tag=p.id))
                except ValueError:
                    pass
            delays = self.singbox.probe_delays(servers)
            if delays:  # ни одного ответа — скорее сбой самой проверки: тогда как раньше, по TCP
                for p in vless:
                    result[p.id] = float(delays[p.id]) if p.id in delays else None
                real = True
        # без sing-box — время TCP-подключения; Hysteria2 работает по UDP, ему оставляем «—»
        rest = [p for p in vless if p.id not in result
                and not p.data.lower().startswith(vpn.HYSTERIA2_SCHEMES)]
        if rest:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(8) as ex:
                for p, ms in zip(rest, ex.map(lambda p: vpn.ping(vpn.parse_vless(p.data)), rest)):
                    result[p.id] = ms
        return result, real

    def vpn_apply(self) -> bool:
        """Перезапуск VPN с новыми настройками или другим сервером, если он включён."""
        with self.lock:
            if self.vpn_running() or (self._vpn_parts and self.settings.get("vpn", "enabled")):
                self._vpn_start()
                return True
            return False

    # ------------------------------------------------------------ обновления
    def check_zapret(self) -> UpdateInfo:
        installed = self.zapret.local_version() if self.zapret.is_installed() else ""
        pinned = self.settings.get("zapret", "pinned_version") or ""
        if pinned and installed:
            # версия выбрана вручную — не обновляем, пока пользователь не вернёт «последнюю»
            return UpdateInfo("zapret", "Zapret", installed, installed, None, pinned=True)
        try:
            rel = github.latest_release(zapret.REPO)
        except Exception as exc:
            try:  # последний запасной вариант — version.txt в репозитории
                ver = github.fetch_text(zapret.VERSION_URL).strip()
                rel = github.Release(zapret.REPO, ver, f"https://github.com/{zapret.REPO}/releases/tag/{ver}")
            except Exception:
                return UpdateInfo("zapret", "Zapret", installed, "", None, str(exc))
        return UpdateInfo("zapret", "Zapret", installed, rel.version, rel)

    def zapret_versions(self) -> List[github.Release]:
        """Версии zapret для выбора вручную: последние релизы, новые первыми."""
        return github.list_releases(zapret.REPO, 20)

    def zapret_install_version(self, rel: github.Release, latest: bool,
                               progress: Optional[ProgressCb] = None) -> None:
        """Ставит выбранную версию zapret. latest — это самая новая: дальше обновляться как обычно;
        иначе версия закрепляется и автообновление zapret её не меняет."""
        cb = (lambda done, total: progress("zapret", done, total)) if progress else None
        with self.lock:
            self._install_zapret(rel, cb)
            self.settings.set("zapret", "pinned_version", "" if latest else rel.version)
            if latest:
                log.info("Zapret: дальше обновляется до последней версии")
            else:
                log.info("Zapret: закреплена версия %s — автообновление её не меняет", rel.version)

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
        if winutil.IS_LINUX and installed:  # встроен в BypassHub — обновляется вместе с ним
            return UpdateInfo("awg", "VPN: AmneziaWG", installed, installed, None)
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
        if self.awg.is_installed() and not winutil.IS_LINUX:  # в Linux AmneziaWG обновляет менеджер пакетов
            infos.append(self.check_awg())
        if self.settings.get("updates", "self_update"):
            infos.append(self.check_self())
        self.settings.set("updates", "last_check", int(time.time()))
        for i in infos:
            if i.error:
                log.warning("%s: не удалось проверить обновления: %s", i.title, i.error)
            elif i.pinned:
                log.info("%s: выбрана версия %s, автообновление выключено", i.title, i.installed)
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

    def _fetch_nfqws(self, cb) -> Optional[Tuple[Path, str]]:
        """Linux: свежий nfqws из релизов bol-van/zapret, если его ещё нет или вышла новая версия.
        None — оставить установленный."""
        try:
            rel = github.latest_release(zapret.NFQWS_REPO)
        except Exception as exc:
            if self.zapret.winws.exists():
                log.warning("nfqws: не удалось проверить обновления (%s), оставляю установленный", exc)
                return None
            raise RuntimeError(f"не удалось скачать nfqws для Linux: {exc}") from None
        if self.zapret.winws.exists() and self.zapret.nfqws_version() == rel.version:
            return None
        asset = rel.asset(zapret.nfqws_asset(rel.tag))
        archive = self.paths.downloads / asset.name
        github.download(asset.url, archive, cb, asset.sha256)
        try:
            dest = self.paths.downloads / zapret.NFQWS
            zapret.extract_nfqws(archive, dest)
        finally:
            archive.unlink(missing_ok=True)
        log.info("nfqws %s скачан", rel.version)
        return dest, rel.version

    def _install_zapret(self, rel: github.Release, cb) -> None:
        name = f"zapret-discord-youtube-{rel.tag}.zip"
        if name not in rel.assets:  # в старых релизах архив мог называться иначе
            name = next((n for n in rel.assets if n.lower().endswith(".zip") and "zapret" in n.lower()), name)
        asset = rel.asset(name)
        zip_path = self.paths.downloads / asset.name
        nfqws = self._fetch_nfqws(cb) if winutil.IS_LINUX else None
        github.download(asset.url, zip_path, cb, asset.sha256)
        try:
            strategy = self.zapret.install_release(zip_path, self.strategy, self.zapret_mode, nfqws=nfqws)
        finally:
            zip_path.unlink(missing_ok=True)
            if nfqws is not None:
                nfqws[0].unlink(missing_ok=True)
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

    @staticmethod
    def _session_started() -> int:
        """Когда начался текущий сеанс: позже из времени загрузки системы и входа пользователя.
        Вход нужен из-за «быстрого запуска» Windows: при выключении ядро уходит в гибернацию, и время
        загрузки после включения прежнее, а вход в систему — новый."""
        import getpass
        import psutil
        started = int(psutil.boot_time())
        try:
            me = getpass.getuser().lower()
            users = psutil.users()
            mine = [u.started for u in users if (u.name or "").split("\\")[-1].lower() == me] or \
                [u.started for u in users]
            if mine:
                started = max(started, int(max(mine)))
        except Exception:
            pass
        return started

    def _first_start_after_boot(self) -> bool:
        """Первый запуск программы после включения компьютера или входа в Windows
        (не перезапуск после обновления)."""
        try:
            boot = self._session_started()
        except Exception:
            return False
        last = int(self.settings.get("app", "last_boot") or 0)
        if abs(boot - last) <= 120:   # время включения ПК может «плавать» на секунды
            return False
        self.settings.set("app", "last_boot", boot)
        return last != 0              # самый первый запуск этой версии — не перезагрузка

    def vpn_off_after_boot(self) -> bool:
        """После включения компьютера VPN выключен: туннель от прошлого сеанса (служба AmneziaWG,
        прокси Windows) мог остаться и делать вид, что работает. True — если что-то выключили."""
        if not self._first_start_after_boot():
            return False
        with self.lock:
            was = bool(self.settings.get("vpn", "enabled")) or self.vpn_running()
            self.settings.set("vpn", "enabled", False)
            self._vpn_parts = ()
            for stop in (self.singbox.stop, self.awg.stop):
                try:
                    stop()
                except Exception as exc:
                    log.warning("VPN после перезагрузки: %r", exc)
            if was:
                log.info("Компьютер был перезагружен — VPN выключен. Включите его, когда понадобится.")
            return was

    def restore_state(self) -> None:
        self.update_learner()
        try:
            self.vpn_off_after_boot()
        except Exception as exc:
            log.warning("VPN после перезагрузки: %r", exc)
        self.start_watchdog()
        if not (self.settings.get("vpn", "enabled") and self.settings.get("app", "restore_state")):
            with self.lock:
                self._killswitch_idle()  # VPN при запуске не включится: строгий режим закрывает интернет сразу
        if not self.awg.status().running:
            try:
                self.awg.restore_dns()  # правило DNS от прежнего туннеля, если программа упала
            except Exception as exc:
                log.debug("Возврат DNS: %r", exc)
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
            with self.lock:
                try:
                    self._vpn_start()
                except Exception as exc:
                    self._vpn_parts = ()
                    self._killswitch_idle()
                    log.error("Не удалось включить VPN: %s", exc)

    def shutdown(self) -> None:
        self.learner.stop()
        self._watch_stop.set()
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
            self._vpn_parts = ()
            try:
                self.singbox.stop()
                self.awg.stop()
            except Exception as exc:
                log.warning("Остановка VPN при выходе: %r", exc)
            self._killswitch_off()
