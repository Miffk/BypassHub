"""Kill switch и переподключение VPN после сбоя."""
import json
import socket
import sys
import time

import pytest

from bypasshub import awg, killswitch, vpn, winutil
from bypasshub.core import Core
from bypasshub.killswitch import KillSwitch, Plan
from bypasshub.paths import Paths
from bypasshub.settings import Settings

from test_vpn import CONF, REALITY


class FakeBackend:
    def __init__(self, fail=False):
        self.plans, self.disabled, self.fail = [], 0, fail

    def enable(self, plan):
        if self.fail:
            raise RuntimeError("нет nftables")
        self.plans.append(plan)

    def disable(self):
        self.disabled += 1


@pytest.fixture
def core(tmp_path, monkeypatch):
    c = Core(Paths(tmp_path / "data"))
    c.vpn_add(REALITY)
    c.state = {"sb": False, "awg": False, "starts": [], "fail": False}

    def sb_start(proxy, name, mode, ru_direct, port, bypass=None, exclude_apps=None, mark=0):
        if c.state["fail"]:
            raise RuntimeError("sing-box завершился сразу после запуска")
        c.state["starts"].append({"mode": mode, "mark": mark, "bypass": bypass})
        c.state["sb"] = True

    monkeypatch.setattr(c, "_ensure_engines", lambda *a: None)
    monkeypatch.setattr(c.singbox, "start", sb_start)
    monkeypatch.setattr(c.singbox, "stop", lambda: c.state.update(sb=False))
    monkeypatch.setattr(c.singbox, "status", lambda: vpn.VpnStatus(running=c.state["sb"], mode="tun"))
    monkeypatch.setattr(c.singbox, "has_group", lambda: False)
    monkeypatch.setattr(c.awg, "stop", lambda: c.state.update(awg=False))
    monkeypatch.setattr(c.awg, "status", lambda: awg.AwgStatus(running=c.state["awg"]))
    c.killswitch._backend = FakeBackend()
    return c


def test_killswitch_follows_vpn(core, monkeypatch):
    monkeypatch.setattr(winutil, "IS_LINUX", True)
    core.settings.set("vpn", "killswitch", True)
    core.vpn_enable()
    plan = core.killswitch._backend.plans[-1]
    assert plan.apps == [core.singbox.exe]
    assert plan.tunnel_addrs == [vpn.TUN_ADDRESS] and plan.tunnel_ifaces == [vpn.INTERFACE_NAME]
    assert core.state["starts"][-1]["mark"] == killswitch.LINUX_MARK   # sing-box помечает свои пакеты
    vs = core.vpn_state()
    assert vs.running and vs.killswitch and not vs.reconnecting
    core.vpn_disable()
    assert not core.killswitch.active and core.killswitch._backend.disabled == 1
    assert not core.vpn_state().killswitch


def test_no_killswitch_when_off_or_proxy(core):
    core.vpn_enable()
    assert core.killswitch._backend.plans == [] and core.state["starts"][-1]["mark"] == 0
    core.vpn_disable()
    core.settings.set("vpn", "killswitch", True)
    core.settings.set("vpn", "mode", "proxy")   # системный прокси: программы и так ходят мимо VPN
    core.vpn_enable()
    assert core.killswitch._backend.plans == [] and not core.killswitch.active


def test_turning_killswitch_off_while_running(core):
    core.settings.set("vpn", "killswitch", True)
    core.vpn_enable()
    assert core.killswitch.active
    core.settings.set("vpn", "killswitch", False)
    assert core.vpn_apply()
    assert not core.killswitch.active


def test_reconnect_after_crash_keeps_killswitch(core):
    core.settings.set("vpn", "killswitch", True)
    core.vpn_enable()
    core.state["sb"] = False                    # sing-box упал
    vs = core.vpn_state()
    assert vs.reconnecting and vs.active and not vs.running and vs.killswitch
    core.state["fail"] = True                   # сервер пока недоступен
    core.watch_once()
    assert core._watch_fails == 1 and core._watch_next > time.monotonic()
    assert core.killswitch.active and core.killswitch._backend.disabled == 0
    core.watch_once()                           # до следующей попытки ещё ждём
    assert core._watch_fails == 1
    core._watch_next = 0
    core.state["fail"] = False
    core.watch_once()
    assert core.state["sb"] and core._watch_fails == 0 and core.killswitch.active
    assert not core.vpn_state().reconnecting


def test_no_reconnect_after_manual_off(core):
    core.vpn_enable()
    core.vpn_disable()
    core.watch_once()
    assert not core.state["sb"] and len(core.state["starts"]) == 1
    assert not core.vpn_state().reconnecting


def test_no_reconnect_for_vpn_not_started_here(core):
    """VPN включён в настройках, но в этом сеансе его не запускали (например, «не включать при
    запуске») — проверка не включает его сама."""
    core.settings.set("vpn", "enabled", True)
    core.watch_once()
    assert core.state["starts"] == [] and not core.vpn_state().reconnecting


def test_killswitch_failure_stops_vpn_enable(core):
    core.settings.set("vpn", "killswitch", True)
    core.killswitch._backend = FakeBackend(fail=True)
    with pytest.raises(RuntimeError, match="Kill switch не включился: нет nftables"):
        core.vpn_enable()
    assert not core.settings.get("vpn", "enabled") and core.state["starts"] == []


def test_awg_split_plan_allows_server(core, monkeypatch):
    monkeypatch.setattr(winutil, "IS_LINUX", False)
    added, _ = core.vpn_add(CONF.replace("$PRIMARY_DNS, $SECONDARY_DNS", "1.1.1.1"))
    core.vpn_select(added[0].id)
    core.settings.set("vpn", "killswitch", True)
    monkeypatch.setattr(awg, "endpoint_ips", lambda conf: ["203.0.113.7"])
    monkeypatch.setattr(core.awg, "start", lambda conf, lan_access=True, split=False:
                        core.state.update(awg=True) or 0)
    core.vpn_enable()   # раздельное туннелирование: туннель + sing-box
    plan = core.killswitch._backend.plans[-1]
    assert plan.endpoints == ["203.0.113.7"] and plan.apps == [core.singbox.exe, core.awg.exe]
    assert plan.tunnel_addrs == [vpn.TUN_ADDRESS, "10.8.1.2/32"]   # и через адаптер AmneziaWG
    assert core._vpn_parts == ("awg", "singbox")
    # имя сервера больше не узнать (DNS закрыт, пока VPN переподключается) — адрес с прошлого раза
    monkeypatch.setattr(awg, "endpoint_ips", lambda conf: (_ for _ in ()).throw(RuntimeError("нет DNS")))
    core.state["awg"] = False
    core.watch_once()
    assert core.state["awg"] and core.state["starts"][-1]["bypass"] == ["203.0.113.7"]


def test_awg_without_split_uses_tunnel_address(core, monkeypatch):
    monkeypatch.setattr(winutil, "IS_LINUX", False)
    added, _ = core.vpn_add(CONF.replace("$PRIMARY_DNS, $SECONDARY_DNS", "1.1.1.1"))
    core.vpn_select(added[0].id)
    core.settings.set("vpn", "killswitch", True)
    core.settings.set("vpn", "ru_direct", False)
    started = []
    monkeypatch.setattr(awg, "endpoint_ips", lambda conf: ["203.0.113.7"])
    monkeypatch.setattr(core.awg, "start", lambda conf, lan_access=True, split=False:
                        started.append(lan_access) or core.state.update(awg=True) or 0)
    core.vpn_enable()
    plan = core.killswitch._backend.plans[-1]
    assert plan.tunnel_addrs == ["10.8.1.2/32"] and plan.apps == [core.awg.exe]
    assert started == [True]          # свой kill switch клиента AmneziaWG не нужен: локальная сеть доступна
    assert core._vpn_parts == ("awg",)


def test_settings_migration_from_awg_lan_access(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"vpn": {"lan_access": False}}), encoding="utf-8")
    assert Settings(p).get("vpn", "killswitch") is True
    p.write_text(json.dumps({"vpn": {"lan_access": False, "killswitch": False}}), encoding="utf-8")
    assert Settings(p).get("vpn", "killswitch") is False
    p.write_text(json.dumps({"vpn": {}}), encoding="utf-8")
    assert Settings(p).get("vpn", "killswitch") is False


def test_nft_rules():
    plan = Plan(endpoints=["203.0.113.7", "2001:db8::1", "bad"], tunnel_ifaces=["bypasshub-vpn"])
    text = killswitch.nft_rules(plan)
    assert f"table inet {killswitch.NFT_TABLE} {{" in text
    assert "type filter hook output priority filter - 10; policy accept;" in text
    assert f"meta mark {killswitch.LINUX_MARK:#x} accept" in text
    assert "meta mark & 0x40000000 != 0 accept" in text
    assert 'oifname { "bypasshub-vpn" } accept' in text
    assert "ip daddr { 203.0.113.7/32 } accept" in text and "ip6 daddr { 2001:db8::1/128 } accept" in text
    assert "192.168.0.0/16" in text and text.rstrip().endswith("reject\n  }\n}")
    script = killswitch._nft_script(plan)
    assert script.startswith(f"table inet {killswitch.NFT_TABLE}\ndelete table inet {killswitch.NFT_TABLE}\n")


def test_zapret_mark_matches_linux():
    from bypasshub import linux
    assert killswitch.ZAPRET_MARK == linux.DESYNC_MARK


def test_linux_backend_uses_helper(monkeypatch):
    from bypasshub import linux
    calls = []

    class Helper:
        def run(self, argv, timeout=60, check=False, input=""):
            calls.append((argv, input))
            return {"rc": 0, "out": ""}

    monkeypatch.setattr(linux, "helper", lambda: Helper())
    monkeypatch.setattr(linux, "which", lambda name: "/usr/bin/" + name)
    b = killswitch._LinuxBackend()
    b.disable()                       # не включали — пароль не спрашиваем
    assert calls == []
    b.enable(Plan())
    b.disable()
    assert calls[0][0] == ["nft", "-f", "-"] and "reject" in calls[0][1]
    assert calls[1][0] == ["nft", "delete", "table", "inet", killswitch.NFT_TABLE]


def test_same_plan_not_reapplied():
    ks = KillSwitch()
    ks._backend = FakeBackend()
    ks.enable(Plan(apps=["a"], tunnel_addrs=["172.19.0.1/30"]))
    ks.enable(Plan(apps=["A"], tunnel_addrs=["172.19.0.1/30"]))
    assert len(ks._backend.plans) == 1
    ks.enable(Plan(apps=["a"], endpoints=["1.2.3.4"]))
    assert len(ks._backend.plans) == 2
    ks.disable()
    ks.disable()
    assert ks._backend.disabled == 1 and not ks.active


def _can_connect(host, port, timeout=5):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not winutil.IS_WINDOWS or not winutil.is_admin(), reason="WFP: Windows с правами администратора")
def test_wfp_blocks_and_releases():
    """Настоящие фильтры WFP: всё мимо закрыто, localhost и разрешённая программа — можно,
    после выключения сеть снова как обычно."""
    if not _can_connect("1.1.1.1", 443):
        pytest.skip("нет доступа к интернету")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    ks = KillSwitch()
    try:
        ks.enable(Plan(apps=[sys.executable], tunnel_addrs=[vpn.TUN_ADDRESS], endpoints=["192.0.2.1"]))
        assert _can_connect("1.1.1.1", 443)          # разрешённая программа (здесь — сам python)
        ks.enable(Plan(apps=[], tunnel_addrs=[vpn.TUN_ADDRESS], endpoints=["192.0.2.1"]))
        assert not _can_connect("1.1.1.1", 443)      # остальным — нельзя
        assert _can_connect("127.0.0.1", port)       # localhost работает
        ks.enable(Plan(apps=[], dns=True))           # строгий режим без VPN: DNS (порт 53) — можно
        assert _can_connect("1.1.1.1", 53) and not _can_connect("1.1.1.1", 443)
    finally:
        ks.disable()
        srv.close()
    assert _can_connect("1.1.1.1", 443)


def test_strict_mode_blocks_when_vpn_off(core):
    """Строгий режим: VPN выключен — интернет закрыт (пропускаются BypassHub, движки VPN, DNS);
    VPN включён — обычный kill switch; выключили строгий режим — интернет открыт."""
    be = core.killswitch._backend
    core.set_killswitch_strict(True)
    assert core.killswitch.active
    offline = be.plans[-1]
    assert offline.dns and core.singbox.exe in offline.apps and core.awg.exe in offline.apps
    assert any(str(p) == sys.executable for p in offline.apps)
    vs = core.vpn_state()
    assert not vs.running and vs.killswitch          # главная: «интернет закрыт»
    core.vpn_enable()                                  # с VPN — план «через VPN», даже без обычного kill switch
    online = be.plans[-1]
    assert not online.dns and online.apps == [core.singbox.exe] and core.killswitch.active
    core.vpn_disable()                                 # выключили VPN — снова закрыт
    assert core.killswitch.active and be.plans[-1].dns
    core.set_killswitch_strict(False)
    assert not core.killswitch.active


def test_strict_mode_on_start_and_failed_enable(core):
    core.settings.set("vpn", "killswitch_strict", True)
    core.restore_state()                               # VPN не включён — интернет закрыт сразу при запуске
    assert core.killswitch.active and core.killswitch._backend.plans[-1].dns
    core._watch_stop.set()
    core.state["fail"] = True
    with pytest.raises(RuntimeError):
        core.vpn_enable()
    assert core.killswitch.active and core.killswitch._backend.plans[-1].dns   # не подключился — закрыт


def test_strict_mode_proxy_vpn_opens(core):
    """Строгий режим и «Системный прокси»: пока VPN подключён, интернет не закрыт (как и обычный kill switch)."""
    core.settings.set("vpn", "mode", "proxy")
    core.set_killswitch_strict(True)
    core.vpn_enable()
    assert not core.killswitch.active
    core.vpn_disable()
    assert core.killswitch.active


def test_nft_rules_dns():
    rules = killswitch.nft_rules(Plan(dns=True))
    assert "udp dport 53 accept" in rules and "tcp dport 53 accept" in rules
    assert "dport 53" not in killswitch.nft_rules(Plan())
