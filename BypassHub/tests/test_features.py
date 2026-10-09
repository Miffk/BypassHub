import json

from bypasshub import applearn, backup, selfupdate, strategy_test
from bypasshub.github import Release
from bypasshub.settings import Settings
from bypasshub.strategy_test import BASELINE, StrategyResult, pick_best
from bypasshub.tgproxy import TgProxyManager
from bypasshub.zapret import GameFilter

from test_zapret import make_zapret


def test_load_targets(tmp_path):
    p = tmp_path / "targets.txt"
    p.write_text('# c\nDiscordMain = "https://discord.com"\nDNS = "PING:1.1.1.1"\nYT="https://youtu.be"\n')
    assert strategy_test.load_targets(p) == ["https://discord.com", "https://youtu.be"]
    assert strategy_test.load_targets(tmp_path / "missing.txt") == strategy_test.DEFAULT_TARGETS


def test_pick_best():
    results = [
        StrategyResult(BASELINE, ok=20, total=24),
        StrategyResult("a.bat", ok=22, total=24, avg_ms=300),
        StrategyResult("b.bat", ok=22, total=24, avg_ms=120),
        StrategyResult("c.bat", ok=0, total=0, error="crash"),
        StrategyResult("d.bat", ok=10, total=24, avg_ms=50),
    ]
    assert pick_best(results).name == "b.bat"
    assert pick_best([StrategyResult(BASELINE, ok=24, total=24)]) is None


def test_applearn_block(tmp_path):
    f = tmp_path / "ipset-exclude-user.txt"
    f.write_text("10.0.0.0/8\n")
    applearn.write_block(f, ["1.2.3.4", "5.6.7.8"], "203.0.113.113/32")
    text = f.read_text()
    assert "10.0.0.0/8" in text and "1.2.3.4" in text and text.count(applearn.BEGIN) == 1
    learner = applearn.AppLearner(f)
    assert learner.learned() == ["1.2.3.4", "5.6.7.8"]
    learner.clear()
    assert f.read_text().strip() == "10.0.0.0/8"
    f.write_text("")
    learner.clear()  # пустой файл не допускается
    assert "203.0.113.113/32" in f.read_text()


def test_is_public():
    assert applearn.is_public("8.8.8.8")
    assert not applearn.is_public("192.168.1.1")
    assert not applearn.is_public("127.0.0.1")
    assert not applearn.is_public("garbage")


def test_selfupdate_version():
    rel = Release("Miffk/games", "bypasshub-v9.9.9", "")
    assert selfupdate.version_of(rel) == "9.9.9"
    assert selfupdate.is_newer(rel)
    assert not selfupdate.is_newer(Release("Miffk/games", "bypasshub-v0.1.0", ""))


def test_backup_roundtrip(tmp_path):
    zm = make_zapret(tmp_path / "a" / "zapret")
    zm.ensure_user_lists()
    zm.save_game_filter(GameFilter("udp", "1000-2000", "3000-4000"))
    zm.set_ipset_mode("loaded")
    zm.write_user_list("list-general-user.txt", "my.site")
    zm.set_active_fake("game", "tls_clienthello_4pda_to")
    tg = TgProxyManager(tmp_path / "a" / "tg")
    cfg = tg.load_config()
    cfg["port"] = 2222
    tg.save_config(cfg)
    s = Settings(tmp_path / "a" / "settings.json")
    s.set("appearance", "colors", ["#123456"])
    s.set("updates", "last_check", 123)
    out = tmp_path / "backup.json"
    backup.export_file(out, s.data, zm, tg)
    assert "last_check" not in json.loads(out.read_text())["bypasshub"]["updates"]

    zm2 = make_zapret(tmp_path / "b" / "zapret")
    tg2 = TgProxyManager(tmp_path / "b" / "tg")
    s2 = Settings(tmp_path / "b" / "settings.json")
    warnings = backup.import_file(out, s2, zm2, tg2)
    assert warnings == []
    assert s2.get("appearance", "colors") == ["#123456"]
    assert tg2.load_config()["port"] == 2222
    assert zm2.load_game_filter().mode == "udp"
    assert zm2.ipset_status() == "loaded"
    assert zm2.read_user_list("list-general-user.txt").strip() == "my.site"
    assert zm2.active_fakes()["game"] == "tls_clienthello_4pda_to"
