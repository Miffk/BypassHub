"""Экспорт и импорт всех настроек в один JSON-файл."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from . import __version__
from .settings import DEFAULTS
from .tgproxy import TgProxyManager
from .zapret import USER_LISTS, GameFilter, ZapretManager

FORMAT = "bypasshub-settings"
# служебные значения, которые не переносятся между компьютерами
_SKIP = {("zapret", "installed_version"), ("tg", "installed_version"), ("updates", "last_check")}


def export_data(settings_data: Dict[str, Any], zapret: ZapretManager, tg: TgProxyManager) -> Dict[str, Any]:
    app = deepcopy(settings_data)
    for section, key in _SKIP:
        app.get(section, {}).pop(key, None)
    z: Dict[str, Any] = {}
    if zapret.is_installed():
        gf = zapret.load_game_filter()
        z = {
            "game_filter": {"mode": gf.mode, "tcp": gf.tcp, "udp": gf.udp},
            "ipset_mode": zapret.ipset_status(),
            "fakes": zapret.active_fakes(),
            "user_lists": {name: zapret.read_user_list(name) for name in USER_LISTS},
        }
    return {
        "format": FORMAT, "version": 1, "app_version": __version__,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bypasshub": app, "zapret": z, "tg_config": tg.load_config(),
    }


def export_file(path: Path, settings_data, zapret, tg) -> None:
    path.write_text(json.dumps(export_data(settings_data, zapret, tg), indent=2, ensure_ascii=False),
                    encoding="utf-8")


def import_file(path: Path, settings, zapret: ZapretManager, tg: TgProxyManager) -> list:
    """Применяет настройки из файла; возвращает список предупреждений."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError("это не файл настроек BypassHub")
    warnings = []
    app = data.get("bypasshub") or {}
    for section, values in app.items():
        if section in DEFAULTS and isinstance(values, dict):
            for key, value in values.items():
                if key in DEFAULTS[section] and (section, key) not in _SKIP:
                    settings.data.setdefault(section, {})[key] = value
    settings.save()

    if isinstance(data.get("tg_config"), dict):
        tg.save_config({**tg.load_config(), **data["tg_config"]})

    z = data.get("zapret") or {}
    if z and not zapret.is_installed():
        warnings.append("zapret ещё не загружен — его настройки не применены. Повторите импорт после загрузки.")
    elif z:
        gf = z.get("game_filter") or {}
        zapret.save_game_filter(GameFilter(gf.get("mode", "disabled"), gf.get("tcp", "1024-65535"),
                                           gf.get("udp", "1024-65535")))
        for name, text in (z.get("user_lists") or {}).items():
            if name in USER_LISTS:
                zapret.write_user_list(name, text)
        try:
            zapret.set_ipset_mode(z.get("ipset_mode", "none"))
        except Exception as exc:
            warnings.append(f"режим IPSet не восстановлен: {exc}")
        for kind, fake in (z.get("fakes") or {}).items():
            if fake:
                try:
                    zapret.set_active_fake(kind, fake)
                except Exception:
                    warnings.append(f"фейк {fake} не найден в текущей версии zapret")
    return warnings
