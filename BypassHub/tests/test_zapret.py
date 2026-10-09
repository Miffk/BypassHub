import shutil
import zipfile
from pathlib import Path

import pytest

from bypasshub import zapret
from bypasshub.zapret import (GameFilter, ZapretManager, expand_vars, parse_strategy,
                              split_cmd_args, validate_ports)

FIXTURES = Path(__file__).parent / "fixtures"


def make_zapret(root: Path, version: str = "1.10.3") -> ZapretManager:
    (root / "bin").mkdir(parents=True)
    (root / "lists").mkdir()
    (root / "utils").mkdir()
    (root / "bin" / "winws.exe").write_bytes(b"MZ")
    for name in ("quic_initial_www_google_com.bin", "stun.bin", "tls_clienthello_4pda_to.bin"):
        (root / "bin" / name).write_bytes(name.encode())
    shutil.copyfile(root / "bin" / "stun.bin", root / "bin" / "ACTIVE_DISCORD_UDP.bin")
    shutil.copyfile(root / "bin" / "stun.bin", root / "bin" / "ACTIVE_GAME_UDP.bin")
    (root / "lists" / "ipset-all.txt").write_text("203.0.113.113/32\n")
    (root / "lists" / "ipset-all.txt.backup").write_text("1.1.1.0/24\n2.2.2.0/24\n")
    (root / "utils" / "check_updates.enabled").write_text("ENABLED")
    (root / "service.bat").write_text(f'@echo off\nset "LOCAL_VERSION={version}"\n')
    for bat in FIXTURES.glob("*.bat"):
        shutil.copyfile(bat, root / bat.name)
    return ZapretManager(root, root.parent / "winws.log")


def test_split_cmd_args_quotes_and_escapes():
    assert split_cmd_args('--a="x y" --b=^! "--c" --d=1,2') == ["--a=x y", "--b=!", "--c", "--d=1,2"]


def test_expand_vars():
    v = {"BIN": "C:\\z\\bin\\", "~dp0": "C:\\z\\"}
    assert expand_vars("%BIN%a.bin %~dp0x %UNKNOWN% 100%%", v) == "C:\\z\\bin\\a.bin C:\\z\\x  100%"


def test_parse_general_bat():
    text = (FIXTURES / "general.bat").read_text()
    gf = GameFilter().variables()
    args = parse_strategy(text, {"~dp0": "C:\\z\\", **gf})
    assert args[0] == "--wf-tcp=80,443,2053,2083,2087,2096,8443,12"
    assert "--hostlist=C:\\z\\lists\\list-general.txt" in args
    assert "--dpi-desync-fake-quic=C:\\z\\bin\\quic_initial_www_google_com.bin" in args
    assert args.count("--new") == 8
    assert not any("%" in a or '"' in a or a == "^" for a in args)


def test_parse_fake_tls_caret_bang():
    text = (FIXTURES / "general (FAKE TLS AUTO).bat").read_text()
    args = parse_strategy(text, {"~dp0": "C:\\z\\", **GameFilter(mode="all").variables()})
    assert "--dpi-desync-fake-tls=!" in args
    assert "--filter-tcp=1024-65535" in args


def test_parse_strategy_without_winws():
    with pytest.raises(ValueError):
        parse_strategy("@echo off\necho hi\n", {})


@pytest.mark.parametrize("value,expected", [
    ("1024-65535", "1024-65535"),
    ("1024-1934, 1936-65535", "1024-1934,1936-65535"),
    ("80", "80"),
    ("0-10", None),
    ("70000", None),
    ("200-100", None),
    ("abc", None),
    ("", None),
])
def test_validate_ports(value, expected):
    assert validate_ports(value) == expected


def test_game_filter_roundtrip(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    assert zm.load_game_filter().mode == "disabled"
    zm.save_game_filter(GameFilter("udp", "1000-2000", "3000-4000"))
    gf = zm.load_game_filter()
    assert (gf.mode, gf.tcp, gf.udp) == ("udp", "1000-2000", "3000-4000")
    assert gf.variables() == {"GameFilter": "3000-4000", "GameFilterTCP": "12", "GameFilterUDP": "3000-4000"}
    # старый формат файла (как в ранних версиях service.bat)
    zm.game_filter_file.write_text("all\n")
    assert zm.load_game_filter().mode == "all"


def test_ipset_modes(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    assert zm.ipset_status() == "none"
    zm.set_ipset_mode("loaded")
    assert zm.ipset_status() == "loaded"
    zm.set_ipset_mode("any")
    assert zm.ipset_status() == "any"
    zm.set_ipset_mode("none")
    assert zm.ipset_status() == "none"
    zm.ipset_backup.unlink()
    with pytest.raises(FileNotFoundError):
        zm.set_ipset_mode("loaded")


def test_fakes(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    assert zm.fake_files() == ["quic_initial_www_google_com", "stun", "tls_clienthello_4pda_to"]
    assert zm.active_fakes() == {"discord": "stun", "game": "stun"}
    zm.set_active_fake("game", "tls_clienthello_4pda_to")
    assert zm.active_fakes()["game"] == "tls_clienthello_4pda_to"


def test_user_lists_never_empty(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    zm.ensure_user_lists()
    zm.write_user_list("list-general-user.txt", "  \n# comment\n")
    assert "domain.example.abc" in zm.read_user_list("list-general-user.txt")
    zm.write_user_list("list-general-user.txt", "example.com\n\n  test.org ")
    assert zm.read_user_list("list-general-user.txt") == "example.com\ntest.org\n"


def test_strategies_and_build_args(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    assert zm.strategies() == ["general (FAKE TLS AUTO).bat", "general.bat"]
    args = zm.build_args("general.bat")
    assert f"--hostlist={zm.lists}/list-general.txt" in args or \
        f"--hostlist={zm.lists}\\list-general.txt" in args
    assert zm.local_version() == "1.10.3"


def _release_zip(tmp_path: Path, version: str) -> Path:
    src = tmp_path / f"build-{version}" / f"zapret-discord-youtube-{version}"
    make_zapret(src, version)
    (src / "lists" / "list-general-user.txt").unlink(missing_ok=True)
    zpath = tmp_path / f"zapret-discord-youtube-{version}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in src.rglob("*"):
            zf.write(f, f.relative_to(src.parent))
    return zpath


def test_install_release_fresh_and_update_keeps_user_data(tmp_path):
    root = tmp_path / "data" / "zapret"
    zm = ZapretManager(root, tmp_path / "winws.log")

    strategy = zm.install_release(_release_zip(tmp_path, "1.10.2"), "general.bat", "process")
    assert strategy == "general.bat"
    assert zm.local_version() == "1.10.2"
    assert not (root / "utils" / "check_updates.enabled").exists()
    assert (root / "lists" / "list-general-user.txt").exists()

    # пользовательские настройки
    zm.write_user_list("list-general-user.txt", "my.site")
    zm.save_game_filter(GameFilter("tcp", "2000-3000", "1024-65535"))
    zm.set_ipset_mode("loaded")
    zm.set_active_fake("discord", "tls_clienthello_4pda_to")
    (root / "my strategy.bat").write_text("winws.exe --x")

    strategy = zm.install_release(_release_zip(tmp_path, "1.10.3"), "general (FAKE TLS AUTO).bat", "process")
    assert strategy == "general (FAKE TLS AUTO).bat"
    assert zm.local_version() == "1.10.3"
    assert zm.read_user_list("list-general-user.txt").strip() == "my.site"
    assert zm.load_game_filter().mode == "tcp"
    assert zm.load_game_filter().tcp == "2000-3000"
    assert zm.ipset_status() == "loaded"
    assert zm.active_fakes()["discord"] == "tls_clienthello_4pda_to"
    assert (root / "my strategy.bat").exists()
    assert not (root.parent / "zapret_old").exists()
    assert not (root.parent / "zapret_new").exists()


def test_install_release_unknown_strategy_falls_back(tmp_path):
    zm = ZapretManager(tmp_path / "zapret", tmp_path / "winws.log")
    assert zm.install_release(_release_zip(tmp_path, "1.0"), "gone.bat", "process") == "general.bat"


def test_hosts_block(tmp_path, monkeypatch):
    zm = ZapretManager(tmp_path / "zapret", tmp_path / "winws.log")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(ZapretManager, "hosts_path", staticmethod(lambda: hosts))
    monkeypatch.setattr(zapret.winutil, "run_logged", lambda *a, **k: 0)
    zm.hosts_apply("1.2.3.4 a.com\n5.6.7.8 b.com\n")
    zm.hosts_apply("1.2.3.4 a.com\n9.9.9.9 c.com\n")
    text = hosts.read_text()
    assert text.count(ZapretManager.HOSTS_BEGIN) == 1
    assert "c.com" in text and "b.com" not in text and "localhost" in text
    assert zm.hosts_remove()
    assert "a.com" not in hosts.read_text() and "localhost" in hosts.read_text()


def test_app_exclusions_block(tmp_path):
    zm = make_zapret(tmp_path / "zapret")
    zm.ensure_user_lists()
    zm.write_user_list("list-exclude-user.txt", "my.site\n")
    zm.set_app_exclusions(["Steam", "FACEIT"], ["Foo.com", " "])
    text = zm.read_user_list("list-exclude-user.txt")
    assert "my.site" in text and "steampowered.com" in text and "faceit.com" in text and "foo.com" in text
    assert zm.app_exclusions() == (["Steam", "FACEIT"], ["foo.com"])
    zm.set_app_exclusions(["Epic Games"], [])
    text = zm.read_user_list("list-exclude-user.txt")
    assert "steampowered.com" not in text and "epicgames.com" in text and "my.site" in text
    assert text.count(zapret.APP_BLOCK_BEGIN) == 1
    zm.set_app_exclusions([], [])
    assert zm.read_user_list("list-exclude-user.txt").strip() == "my.site"
    # пустой файл недопустим — подставляется заглушка
    zm.write_user_list("list-exclude-user.txt", "")
    zm.set_app_exclusions([], [])
    assert "domain.example.abc" in zm.read_user_list("list-exclude-user.txt")
