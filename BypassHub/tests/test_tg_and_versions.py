import json

from bypasshub import github, tgproxy
from bypasshub.settings import Settings
from bypasshub.tgproxy import TgProxyManager, validate


def _form(**over):
    values = {
        "host": "127.0.0.1", "port": "1443", "secret": "a" * 32,
        "dc_ip": "2:149.154.167.220\n4:149.154.167.220\n",
        "buf_kb": "256", "pool_size": "4", "log_max_mb": "5",
        "cfproxy_user_domain": "a.com, b.com;a.com", "cfproxy_worker_domain": "",
    }
    values.update(over)
    return values


def test_validate_ok():
    cfg = validate(_form())
    assert isinstance(cfg, dict)
    assert cfg["port"] == 1443 and cfg["dc_ip"] == ["2:149.154.167.220", "4:149.154.167.220"]
    assert cfg["cfproxy_user_domain"] == ["a.com", "b.com"]
    assert cfg["buf_kb"] == 256 and isinstance(cfg["log_max_mb"], float)


def test_validate_errors():
    assert isinstance(validate(_form(host="localhost")), str)
    assert isinstance(validate(_form(port="70000")), str)
    assert isinstance(validate(_form(secret="xyz")), str)
    assert isinstance(validate(_form(secret="g" * 32)), str)
    assert isinstance(validate(_form(dc_ip="2-1.1.1.1")), str)
    assert isinstance(validate(_form(dc_ip="2:999.1.1.1")), str)
    assert validate(_form(buf_kb="abc"))["buf_kb"] == 256


def test_config_roundtrip_forces_manager_owned_keys(tmp_path):
    tg = TgProxyManager(tmp_path / "tg")
    tg.data_dir.mkdir(parents=True)
    tg.config_file.write_text(json.dumps({"port": 2000, "check_updates": True, "autostart": True,
                                          "cfproxy_user_domain": ["x.com"], "secret": "b" * 32}))
    cfg = tg.load_config()
    assert cfg["port"] == 2000 and cfg["check_updates"] is False and cfg["autostart"] is False
    assert cfg["cfproxy_user_domain_enabled"] is True
    assert cfg["h2"] is True  # значение по умолчанию добавилось
    tg.ensure_config()
    saved = json.loads(tg.config_file.read_text(encoding="utf-8"))
    assert saved["check_updates"] is False
    assert (tg.data_dir / ".first_run_done_mtproto").exists()


def test_new_config_gets_secret(tmp_path):
    cfg = TgProxyManager(tmp_path / "tg").load_config()
    assert len(cfg["secret"]) == 32


def test_proxy_link():
    assert tgproxy.proxy_link({"host": "127.0.0.1", "port": 1443, "secret": "ab" * 16}) == \
        "tg://proxy?server=127.0.0.1&port=1443&secret=dd" + "ab" * 16


def test_choose_asset_explicit():
    assert tgproxy.choose_asset("TgWsProxy_windows_arm64.exe") == "TgWsProxy_windows_arm64.exe"
    assert tgproxy.choose_asset("auto") in tgproxy.ASSETS


def test_versions():
    assert github.is_newer("v1.11.1", "1.11.0")
    assert github.is_newer("1.10.10", "1.10.9")
    assert not github.is_newer("v1.11.1", "1.11.1")
    assert not github.is_newer("1.9", "1.10")
    assert github.is_newer("1.0", "")


def test_release_asset_fallback_url():
    rel = github.Release("Flowseal/tg-ws-proxy", "v1.2.3", "")
    assert rel.version == "1.2.3"
    assert rel.asset("TgWsProxy_windows.exe").url == \
        "https://github.com/Flowseal/tg-ws-proxy/releases/download/v1.2.3/TgWsProxy_windows.exe"


def test_settings_merge_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"zapret": {"strategy": "x.bat"}, "unknown": 1}))
    s = Settings(p)
    assert s.get("zapret", "strategy") == "x.bat"
    assert s.get("zapret", "mode") == "process"
    assert s.get("appearance", "mode") == "dark"
    s.set("appearance", "colors", ["#ff0000", "#00ff00"])
    assert json.loads(p.read_text(encoding="utf-8"))["appearance"]["colors"] == ["#ff0000", "#00ff00"]
