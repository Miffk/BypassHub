"""Linux-версия: nfqws вместо winws, правила перехвата, systemd, помощник с правами root, ярлыки, прокси."""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from bypasshub import awg, linux, selfupdate, tgproxy, vpn, winutil, zapret
from bypasshub.zapret import ZapretManager, to_nfqws_args

from test_zapret import FIXTURES, make_zapret

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="только Linux")


class FakeHelper:
    """Вместо помощника с правами root: запоминает команды и выполняет запись файлов."""

    def __init__(self):
        self.calls = []
        self.alive = set()

    def running(self):
        return True

    def call(self, cmd, **args):
        self.calls.append((cmd, args))
        if cmd == "write_file":
            Path(args["path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(args["path"]).write_text(args["text"], encoding="utf-8")
            return True
        if cmd == "spawn":
            self.alive.add(4242)
            return {"pid": 4242}
        if cmd == "poll":
            return None if args["pid"] in self.alive else 1
        if cmd in ("zapret_fw", "run"):
            return {"rc": 0, "out": ""}
        return True

    def run(self, argv, timeout=60, check=False, input=""):
        return self.call("run", argv=list(argv), timeout=timeout)

    def names(self):
        return [c for c, _ in self.calls]


@pytest.fixture
def fake_helper(monkeypatch):
    h = FakeHelper()
    monkeypatch.setattr(linux, "helper", lambda: h)
    return h


# ---------------------------------------------------------------------- стратегии → nfqws

def test_to_nfqws_args_moves_windivert_filter_to_ports():
    args = ["--wf-tcp=80,443,12", "--wf-udp=443,50000-50100", "--filter-udp=443", "--dpi-desync=fake",
            "--new", "--wf-l3=ipv4", "--ssid-filter=home", "--filter-tcp=443"]
    out, tcp, udp = to_nfqws_args(args)
    assert out == ["--filter-udp=443", "--dpi-desync=fake", "--new", "--filter-tcp=443"]
    assert (tcp, udp) == ("80,443,12", "443,50000-50100")
    out, tcp, _ = to_nfqws_args(["--wf-tcp", "80", "--new"])  # значение отдельным аргументом
    assert out == ["--new"] and tcp == "80"


@linux_only
def test_linux_launch_from_real_strategy(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    argv, tcp, udp = zm.linux_launch("general.bat")
    assert argv[0] == str(zm.root / "bin" / "nfqws")
    assert argv[1] == f"--qnum={linux.QNUM}" and argv[2] == f"--uid={os.getuid()}:{os.getgid()}"
    assert not any(a.startswith("--wf-") for a in argv)
    assert f"--hostlist={zm.lists}/list-general.txt" in argv
    assert "443" in tcp.split(",") and "443" in udp.split(",")
    assert zm.is_installed() and zm.engine_name == "nfqws"
    assert "--qnum" in zm.command_line("general.bat")


@linux_only
def test_zapret_not_installed_without_nfqws(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    (zm.bin / "nfqws").unlink()
    assert not zm.is_installed()  # стратегии есть, а программы обхода нет — нужна установка


def test_merge_ports():
    assert linux.merge_ports("443,80,1024-65535,50000-50100,12,~5") == [(12, 12), (80, 80), (443, 443),
                                                                         (1024, 65535)]
    assert linux.merge_ports("10-20,21,30") == [(10, 21), (30, 30)]
    assert linux.merge_ports("") == [] and linux.merge_ports("abc,0,70000") == []


def test_multiport_chunks_limit():
    ranges = [(p, p) for p in range(1, 21)]
    chunks = linux._multiport_chunks(ranges)
    assert len(chunks) == 2 and len(chunks[0].split(",")) == 15
    assert linux._multiport_chunks([(1000, 2000)]) == ["1000:2000"]


def test_firewall_script(tmp_path):
    script = linux.firewall_script("80,443,1024-65535", "443,19294-19344,1024-65535")
    assert "tcp dport { 80, 443, 1024-65535 }" in script
    assert "udp dport { 443, 1024-65535 }" in script  # пересекающиеся диапазоны слиты
    assert "queue num 200 bypass" in script and "meta mark & 0x40000000 != 0 accept" in script
    assert "--dports 80,443,1024:65535" in script and "--queue-bypass" in script
    path = tmp_path / "fw.sh"
    path.write_text(script, encoding="utf-8")
    if shutil.which("sh"):
        assert subprocess.run(["sh", "-n", str(path)]).returncode == 0  # синтаксис sh


def test_systemd_unit():
    assert linux.systemd_quote("/a/b") == "/a/b"
    assert linux.systemd_quote("--x=/home/u/my lists/a.txt") == '"--x=/home/u/my lists/a.txt"'
    assert linux.systemd_quote("100%") == "100%%" and linux.systemd_quote("$HOME") == "$$HOME"
    unit = linux.unit_text("BypassHub: zapret", ["/usr/local/lib/bypasshub/nfqws", "--qnum=200"],
                           pre=["/bin/sh /etc/bypasshub/zapret-fw.sh up"],
                           post=["/bin/sh /etc/bypasshub/zapret-fw.sh down"], comment="strategy: general")
    assert "ExecStart=/usr/local/lib/bypasshub/nfqws --qnum=200" in unit
    assert "ExecStartPre=/bin/sh /etc/bypasshub/zapret-fw.sh up" in unit
    assert "# strategy: general" in unit and "WantedBy=multi-user.target" in unit


@linux_only
def test_zapret_start_and_stop_through_helper(tmp_path, fake_helper, monkeypatch):
    zm = make_zapret(tmp_path / "zapret")
    monkeypatch.setattr(zapret.time, "sleep", lambda s: None)
    monkeypatch.setattr(linux, "unit_state", lambda unit: None)
    monkeypatch.setattr(winutil, "find_processes", lambda name: [])
    zm.start_process("general.bat")
    assert fake_helper.names() == ["zapret_fw", "zapret_fw", "spawn", "poll"]  # down (kill_winws), up, запуск
    up = fake_helper.calls[1][1]
    assert up["action"] == "up" and "443" in up["tcp"].split(",")
    spawn = fake_helper.calls[2][1]
    assert spawn["argv"][0] == str(zm.winws) and spawn["log"] == str(zm.winws_log)

    fake_helper.alive.clear()  # nfqws сразу завершился — правила убираются, ошибка с логом
    fake_helper.calls.clear()
    monkeypatch.setattr(FakeHelper, "call", lambda self, cmd, **a: (self.calls.append((cmd, a)), 1 if cmd == "poll"
                                                                       else {"rc": 0, "out": "", "pid": 1})[1])
    zm.winws_log.write_text("can't bind queue")
    with pytest.raises(RuntimeError, match="can't bind queue"):
        zm.start_process("general.bat")
    assert fake_helper.calls[-1] == ("zapret_fw", {"action": "down"})


@linux_only
def test_zapret_service_unit(tmp_path, fake_helper, monkeypatch):
    zm = make_zapret(tmp_path / "zapret")
    monkeypatch.setattr(linux, "UNIT_DIR", tmp_path / "systemd")
    monkeypatch.setattr(zapret, "LINUX_FW_SCRIPT", tmp_path / "etc" / "zapret-fw.sh")
    monkeypatch.setattr(zapret.time, "sleep", lambda s: None)
    monkeypatch.setattr(winutil, "find_processes", lambda name: [])
    monkeypatch.setattr(linux, "unit_state", lambda unit: "RUNNING")
    orig = fake_helper.call
    fake_helper.call = lambda cmd, **a: "/usr/local/lib/bypasshub/nfqws" if cmd == "install_bin" else orig(cmd, **a)
    zm.install_service("general (FAKE TLS AUTO).bat")
    unit = (tmp_path / "systemd" / zapret.LINUX_UNIT).read_text()
    assert "ExecStart=/usr/local/lib/bypasshub/nfqws --qnum=200" in unit
    assert zm.service_strategy() == "general (FAKE TLS AUTO)"
    assert "nft" in (tmp_path / "etc" / "zapret-fw.sh").read_text()
    assert ["systemctl", "enable", "--now", zapret.LINUX_UNIT] in [a.get("argv") for c, a in fake_helper.calls]


@linux_only
def test_hosts_on_linux(tmp_path, fake_helper, monkeypatch):
    zm = ZapretManager(tmp_path / "zapret", tmp_path / "winws.log")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(linux, "HOSTS", hosts)
    monkeypatch.setattr(zapret, "LINUX_HOSTS_BACKUP", tmp_path / "etc" / "hosts.bak")
    monkeypatch.setattr(linux, "which", lambda name: None)
    zm.hosts_apply("1.2.3.4 a.com\n")
    assert "a.com" in hosts.read_text() and "\r\n" not in hosts.read_text()
    assert (tmp_path / "etc" / "hosts.bak").read_text() == "127.0.0.1 localhost\n"
    assert zm.hosts_remove() and hosts.read_text() == "127.0.0.1 localhost\n"


def _tar(path: Path, members: dict) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_extract_nfqws(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, "machine", lambda: "amd64")
    archive = _tar(tmp_path / "z.tar.gz", {"zapret-v72.9/binaries/linux-arm64/nfqws": b"arm",
                                          "zapret-v72.9/binaries/linux-x86_64/nfqws": b"x86"})
    dest = tmp_path / "out" / "nfqws"
    zapret.extract_nfqws(archive, dest)
    assert dest.read_bytes() == b"x86" and os.access(dest, os.X_OK)
    assert zapret.nfqws_asset("v72.9") == "zapret-v72.9-openwrt-embedded.tar.gz"
    with pytest.raises(IOError):
        zapret.extract_nfqws(_tar(tmp_path / "e.tar.gz", {"a/b": b""}), dest)


@linux_only
def test_install_release_puts_fresh_nfqws(tmp_path):
    from test_zapret import _release_zip
    zm = ZapretManager(tmp_path / "data" / "zapret", tmp_path / "winws.log")
    fresh = tmp_path / "nfqws-new"
    fresh.write_bytes(b"NEW")
    zm.install_release(_release_zip(tmp_path, "1.10.2"), "general.bat", "process", nfqws=(fresh, "72.9"))
    assert zm.winws.read_bytes() == b"NEW" and zm.nfqws_version() == "72.9"
    zm.install_release(_release_zip(tmp_path, "1.10.3"), "general.bat", "process")  # без нового — переносится
    assert zm.local_version() == "1.10.3" and zm.nfqws_version() == "72.9"


# ---------------------------------------------------------------------- VPN, AmneziaWG, TG, самообновление

@linux_only
def test_singbox_linux_asset_and_install(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, "machine", lambda: "amd64")
    assert vpn.asset_name("1.14.3") == "sing-box-1.14.3-linux-amd64.tar.gz"
    assert vpn.EXE_NAME == "sing-box" and " " not in vpn.INTERFACE_NAME and len(vpn.INTERFACE_NAME) <= 15
    archive = _tar(tmp_path / "sb.tar.gz", {"sing-box-1.14.3-linux-amd64/sing-box": b"ELF",
                                           "sing-box-1.14.3-linux-amd64/LICENSE": b"x"})
    monkeypatch.setattr(vpn.github, "download",
                        lambda url, dest, *a, **k: dest.parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copyfile(archive, dest))
    m = vpn.VpnManager(tmp_path / "sb", tmp_path / "vpn.log")
    monkeypatch.setattr(m, "update_rule_sets", lambda **k: None)
    m.install_release(vpn.github.Release("SagerNet/sing-box", "v1.14.3", ""), tmp_path / "dl")
    assert m.exe.read_bytes() == b"ELF" and os.access(m.exe, os.X_OK) and m.local_version() == "1.14.3"


@linux_only
def test_exclude_apps_linux_names():
    assert vpn.clean_exe_names(["/usr/bin/Steam", "cs2", "cs2", ""]) == ["steam", "cs2"]
    rule = vpn.exclude_rule(["cs2"])
    assert rule == {"process_path_regex": ["(?i)(^|[\\\\/])cs2$"], "outbound": "direct"}


@linux_only
def test_singbox_tun_runs_through_helper(tmp_path, fake_helper, monkeypatch):
    m = vpn.VpnManager(tmp_path / "sb", tmp_path / "vpn.log")
    m.root.mkdir()
    m.exe.write_bytes(b"x")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    poll = m._launch("tun")
    assert fake_helper.calls[0][0] == "spawn" and fake_helper.calls[0][1]["argv"][:2] == [str(m.exe), "run"]
    assert poll() is None


def test_linux_proxy_commands():
    kde = ("/usr/bin/kreadconfig6", "/usr/bin/kwriteconfig6")
    cmds = linux.proxy_commands(2080, None, kde, "/usr/bin/gsettings")
    flat = [" ".join(c) for c in cmds]
    assert any("--key ProxyType 1" in c for c in flat)
    assert any("--key httpProxy http://127.0.0.1 2080" in c for c in flat)
    assert any(c.endswith("org.gnome.system.proxy mode 'manual'") for c in flat)
    assert any(c.endswith("org.gnome.system.proxy.socks port 2080") for c in flat)
    snap = {"kde": {"ProxyType": "0", "httpProxy": ""}, "gnome": {"mode": "'none'"}}
    back = [" ".join(c) for c in linux.proxy_commands(0, snap, kde, "/usr/bin/gsettings")]
    assert any("--key ProxyType 0" in c for c in back) and any("--key httpProxy --delete" in c for c in back)
    assert any(c.endswith("org.gnome.system.proxy mode 'none'") for c in back)


@linux_only
def test_awg_linux_start_stop(tmp_path, fake_helper, monkeypatch):
    from test_vpn import CONF
    monkeypatch.setattr(linux, "which", lambda name: f"/usr/bin/{name}" if name != "resolvconf" else None)
    up = {"v": False}
    monkeypatch.setattr(awg.AwgManager, "_linux_up", staticmethod(lambda: up["v"]))
    m = awg.AwgManager(tmp_path)
    assert m.is_installed()
    m.start(awg.parse_conf(CONF), split=True)
    conf = m.conf_file.read_text()
    assert "Table = off" in conf and "DNS" not in conf and oct(m.conf_file.stat().st_mode & 0o777) == "0o600"
    argvs = [a["argv"] for c, a in fake_helper.calls if c == "run"]
    assert ["awg-quick", "up", str(m.conf_file)] in argvs
    assert any(a[:3] == ["ip", "route", "replace"] for a in argvs)
    up["v"] = True
    m.stop()
    assert ["awg-quick", "down", str(m.conf_file)] in [a["argv"] for c, a in fake_helper.calls if c == "run"]


def test_awg_split_dns():
    conf, dns = awg.split_dns("[Interface]\nDNS = 1.1.1.1, 8.8.8.8\nAddress = 10.0.0.2/32\n")
    assert dns == ["1.1.1.1", "8.8.8.8"] and "DNS" not in conf and "Address" in conf


@linux_only
def test_awg_missing_tools_hint(monkeypatch):
    monkeypatch.setattr(linux, "which", lambda name: None)
    assert not awg.AwgManager(Path("/nonexistent")).is_installed()
    with pytest.raises(RuntimeError, match="amneziawg-tools"):
        awg.latest_release()


@linux_only
def test_tg_linux_asset():
    assert tgproxy.choose_asset("auto") == "TgWsProxy_linux_amd64"
    assert tgproxy.choose_asset("TgWsProxy_windows.exe") == "TgWsProxy_linux_amd64"
    assert tgproxy.EXE_NAME == "TgWsProxy"


@linux_only
def test_selfupdate_linux_names(tmp_path):
    assert selfupdate.ASSET == "BypassHub-linux-x86_64"
    exe = tmp_path / "BypassHub-linux-x86_64"
    assert selfupdate._neighbour(exe, "old").name == "BypassHub-linux-x86_64.old"


# ---------------------------------------------------------------------- рабочий стол

def test_desktop_entry_quoting():
    text = linux.desktop_entry(["/home/u/My Apps/BypassHub", "--minimized"], "bypasshub", autostart=True)
    assert 'Exec="/home/u/My Apps/BypassHub" --minimized' in text
    assert "Icon=bypasshub" in text and "X-GNOME-Autostart-enabled=true" in text
    assert linux._desktop_quote("100%") == "100%%"


def test_autostart_and_menu_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert not linux.autostart_enabled()
    linux.set_autostart(True)
    assert linux.autostart_enabled() and "--minimized" in linux.autostart_file().read_text()
    linux.install_menu_entry(b"PNG")
    assert (tmp_path / "data" / "applications" / "bypasshub.desktop").exists()
    assert (tmp_path / "data" / "icons" / "hicolor" / "256x256" / "apps" / "bypasshub.png").read_bytes() == b"PNG"
    linux.set_autostart(False)
    assert not linux.autostart_enabled()


@linux_only
def test_single_instance_lock(tmp_path):
    lock = tmp_path / "instance.lock"
    code = ("import sys; sys.path.insert(0, sys.argv[2]); from bypasshub import linux; from pathlib import Path; "
            "print(linux.acquire_single_instance(Path(sys.argv[1])))")
    root = str(Path(__file__).resolve().parent.parent)
    assert linux.acquire_single_instance(lock)
    out = subprocess.run([sys.executable, "-c", code, str(lock), root], capture_output=True, text=True).stdout
    assert out.strip() == "False"  # вторая копия не запускается


def test_parse_ufw_numbered():
    text = ("Status: active\n\n     To                         Action      From\n"
            "[ 1] 22/tcp                     ALLOW IN    Anywhere\n"
            "[ 2] 1443/tcp                   ALLOW IN    Anywhere                   # BypassHub TG WS Proxy\n"
            "[10] 1443/tcp (v6)              ALLOW IN    Anywhere (v6)              # BypassHub TG WS Proxy\n")
    assert linux.parse_ufw_numbered(text, "BypassHub TG WS Proxy") == [2, 10]


# ---------------------------------------------------------------------- помощник с правами root

@linux_only
def test_helper_whitelists(tmp_path):
    linux.set_data_root(tmp_path)
    prog = tmp_path / "zapret" / "bin" / "nfqws"
    prog.parent.mkdir(parents=True)
    prog.write_bytes(b"x")
    assert linux._allowed_exe(str(prog))
    assert not linux._allowed_exe("/bin/sh")
    assert linux._allowed_file("/etc/hosts") and linux._allowed_file("/etc/bypasshub/zapret-fw.sh")
    assert linux._allowed_file("/etc/systemd/system/bypasshub-zapret.service")
    assert not linux._allowed_file("/etc/systemd/system/sshd.service")
    assert not linux._allowed_file("/etc/passwd") and not linux._allowed_file("/etc/bypasshub/../shadow")
    with pytest.raises(PermissionError):
        linux.dispatch("run", {"argv": ["sh", "-c", "id"]}, 0, 0)
    with pytest.raises(PermissionError):
        linux.dispatch("spawn", {"argv": ["/bin/sh"]}, 0, 0)


@pytest.mark.skipif(not sys.platform.startswith("linux") or os.geteuid() != 0,
                    reason="помощник запускается только от root")
def test_helper_protocol_end_to_end(tmp_path):
    """Окно ↔ помощник по каналу: запуск программы из папки данных, проверка, остановка."""
    prog = tmp_path / "bin" / "sleeper"  # своя копия программы в папке данных — как nfqws или sing-box
    prog.parent.mkdir()
    shutil.copy2(shutil.which("sleep"), prog)
    main = Path(__file__).resolve().parent.parent / "main.py"
    proc = subprocess.Popen([sys.executable, str(main), "--root-helper", str(tmp_path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=dict(os.environ, PKEXEC_UID=str(os.getuid())))

    def ask(i, cmd, **args):
        proc.stdin.write((json.dumps({"id": i, "cmd": cmd, "args": args}) + "\n").encode())
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        assert json.loads(proc.stdout.readline())["result"] == "ready"
        assert ask(1, "ping")["result"] == "pong"
        log_file = tmp_path / "sleeper.log"
        pid = ask(2, "spawn", argv=[str(prog), "30"], cwd=str(tmp_path), log=str(log_file))["result"]["pid"]
        assert ask(3, "poll", pid=pid)["result"] is None
        bad = ask(4, "run", argv=["bash", "-c", "id"])
        assert not bad["ok"] and "не разрешена" in bad["error"]
        assert ask(5, "kill", pid=pid)["ok"]
        import time
        for _ in range(50):
            if ask(6, "poll", pid=pid)["result"] is not None:
                break
            time.sleep(0.1)
        assert ask(7, "poll", pid=pid)["result"] is not None
        assert log_file.exists()
        assert not ask(8, "kill", pid=os.getpid())["ok"]  # чужие программы не останавливает
    finally:
        proc.stdin.close()
        assert proc.wait(10) == 0
