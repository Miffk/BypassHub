"""Страница TG WS Proxy (как qtui/tg_page.py): подключение, датацентры, Cloudflare, тонкие настройки."""
from __future__ import annotations

from typing import Any, Dict

from ... import tgproxy, winutil
from ...tgproxy import DEFAULT_CONFIG
from .. import ui
from .home import tg_state, _tg_open
from .sheets import PhoneSheet

APPEARANCE = {"auto": "Как в системе", "dark": "Тёмная", "light": "Светлая"}
LANGUAGES = {"ru": "Русский", "en": "English"}


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def build(app):
    cfg: Dict[str, Any] = app.page_state["tg"].get("draft") or app.core.tg.load_config()
    head = tg_state(app)
    color = "yellow" if head.get("busy") or head.get("warn") else ("green" if head["on"] else "muted")
    ts = app.ts
    details = f"Версия {ts.version}" if ts.installed else ""
    if ts.foreign:
        details += ". Запущена другая копия TgWsProxy — закройте её перед включением: " + ", ".join(ts.foreign)
    blocks = [ui.service("tg", "TG WS Proxy", "send", head["on"], head["status"] + (
        f" на {cfg.get('host')}:{cfg.get('port')}" if head["on"] and not head.get("warn") and not head.get("busy")
        else ""), color, "Ускоряет Telegram Desktop: локальный MTProto-прокси", details, busy=bool(head.get("busy")),
        buttons=[ui.button("tg.open", "Открыть в Telegram", primary=True), ui.button("copy", "Скопировать ссылку",
                                                                                     icon="copy"),
                 ui.button("phone", "Подключить телефон", icon="phone")])]
    blocks.append(ui.section("Подключение", [
        ui.row("Адрес и порт", ui.field("host", _s(cfg.get("host"))), ui.field("port", _s(cfg.get("port")), width=100)),
        ui.field("secret", _s(cfg.get("secret")), "Secret", mono=True,
                 buttons=[ui.button("secret.new", "Новый", small=True)]),
        ui.buttons(ui.button("save", "Сохранить", primary=True, collect=True, arg="main"),
                   ui.button("revert", "Отменить изменения"), ui.button("restart", "Перезапустить"),
                   ui.button("log", "Лог")),
    ], help="Адрес, на котором прокси принимает подключения Telegram Desktop. Для доступа из локальной сети "
            "укажите 0.0.0.0 (или нажмите «Подключить телефон»)."))
    blocks.append(ui.group("Дополнительно"))
    blocks.append(ui.section("Датацентры (DC → IP)", [
        ui.textarea("dc_ip", "\n".join(cfg.get("dc_ip") or []), rows=3),
        ui.buttons(ui.button("save", "Сохранить", primary=True, collect=True, arg="dc")),
    ], help="По одному правилу на строку, формат: номер:IP. Если не грузятся фото и видео — оставьте только "
            "4:149.154.167.220 или очистите поле.", collapsible=True, key="tg.dc"))
    blocks.append(ui.section("Cloudflare", [
        ui.toggle("cfproxy", "CF-прокси", cfg.get("cfproxy", True), local=True),
        ui.toggle("h2", "Мультиплексирование медиа (HTTP/2)", cfg.get("h2", True), local=True),
        ui.toggle("cfproxy_user_domain_enabled", "Свой домен", cfg.get("cfproxy_user_domain_enabled", False), local=True),
        ui.field("cfproxy_user_domain", ", ".join(tgproxy.coerce_domain_list(cfg.get("cfproxy_user_domain"))),
                 "Домены", "через запятую"),
        ui.toggle("cfproxy_worker_enabled", "Cloudflare Worker", cfg.get("cfproxy_worker_enabled", False), local=True),
        ui.field("cfproxy_worker_domain", ", ".join(tgproxy.coerce_domain_list(cfg.get("cfproxy_worker_domain"))),
                 "Worker-домены", "через запятую"),
        ui.buttons(ui.button("save", "Сохранить", primary=True, collect=True, arg="cf")),
    ], help="Запасной путь через Cloudflare, если прямое WebSocket-подключение недоступно. Свой домен — "
            "docs/RU/CfProxy.md, Worker (бесплатный аналог своего домена) — docs/RU/CfWorker.md в репозитории "
            "tg-ws-proxy.", collapsible=True, key="tg.cf",
        note="включён" if cfg.get("cfproxy", True) else "выключен"))
    blocks.append(ui.section("Тонкие настройки", [
        ui.toggle("verbose", "Подробный лог (verbose)", cfg.get("verbose", False), local=True),
        ui.toggle("no_secure", "Без TLS для CF-прокси и CF-worker", cfg.get("no_secure", False), local=True),
        ui.toggle("force_test_dc", "Тестовые датацентры (для разработчиков)", cfg.get("force_test_dc", False),
                  local=True),
        ui.field("buf_kb", _s(cfg.get("buf_kb")), "Буфер, КБ", "256", width=120),
        ui.field("pool_size", _s(cfg.get("pool_size")), "Пул WebSocket-сессий", "4", width=120),
        ui.field("log_max_mb", _s(cfg.get("log_max_mb")), "Размер лога, МБ", "5", width=120),
        ui.segmented("appearance", list(APPEARANCE.values()), APPEARANCE.get(cfg.get("appearance", "auto"),
                                                                             APPEARANCE["auto"]),
                     "Тема окон прокси", local=True),
        ui.segmented("language", list(LANGUAGES.values()), LANGUAGES.get(cfg.get("language", "ru"), LANGUAGES["ru"]),
                     "Язык окон прокси", local=True),
        ui.buttons(ui.button("save", "Сохранить", primary=True, collect=True, arg="advanced"),
                   ui.button("defaults", "По умолчанию",
                             confirm="Вернуть настройки прокси по умолчанию? Secret сохранится.")),
    ], collapsible=True, key="tg.advanced"))
    return ui.page("TG WS Proxy", blocks)


FIELDS = ("host", "port", "secret", "dc_ip", "cfproxy_user_domain", "cfproxy_worker_domain", "buf_kb", "pool_size",
          "log_max_mb")
TOGGLES = ("cfproxy", "h2", "cfproxy_user_domain_enabled", "cfproxy_worker_enabled", "verbose", "no_secure",
           "force_test_dc")


def _save(app, form):
    form = dict(form or {})
    cfg = app.core.tg.load_config()
    for k in FIELDS:
        if k in form:
            cfg[k] = form[k]
    for k in TOGGLES:
        if k in form:
            cfg[k] = bool(form[k])
    if "appearance" in form:
        cfg["appearance"] = next((k for k, v in APPEARANCE.items() if v == form["appearance"]), "auto")
    if "language" in form:
        cfg["language"] = next((k for k, v in LANGUAGES.items() if v == form["language"]), "ru")
    result = tgproxy.validate(cfg)
    if isinstance(result, str):
        return ui.error(result, "Настройки прокси")
    if result["cfproxy_user_domain_enabled"] and not result["cfproxy_user_domain"]:
        return ui.error("Включён «Свой домен», но домен не указан.", "Настройки прокси")
    if result["cfproxy_worker_enabled"] and not result["cfproxy_worker_domain"]:
        return ui.error("Включён Cloudflare Worker, но домен не указан.", "Настройки прокси")
    app.core.tg.save_config(result)
    app.page_state["tg"].pop("draft", None)
    try:  # адрес 0.0.0.0 — нужен доступ из сети: правило брандмауэра добавляется/удаляется само
        app.core.tg.sync_firewall(result)
    except Exception as exc:
        app.alert(str(exc), "Брандмауэр", "error")
    app.run("tg", app.core.tg_apply,
            lambda restarted: "Настройки сохранены" + (", прокси перезапущен" if restarted else ""), "Применение…",
            "TG WS Proxy")
    return {"clear": list(FIELDS)}


def _defaults(app, _):
    cfg = dict(DEFAULT_CONFIG)
    cfg["secret"] = app.core.tg.load_config().get("secret") or tgproxy.new_secret()
    app.page_state["tg"]["draft"] = cfg
    return ui.done("Подставлены значения по умолчанию — нажмите «Сохранить»", clear=list(FIELDS))


def _revert(app, _):
    app.page_state["tg"].pop("draft", None)
    return {"clear": list(FIELDS)}


def _restart(app, _):
    def work():
        app.core.settings.set("tg", "enabled", True)
        app.core.tg.restart()
    app.run("tg", work, lambda _: "Прокси перезапущен", "Перезапуск…", "TG WS Proxy")


def _log(app, _):
    p = app.core.tg.log_file
    if p.exists():
        winutil.open_path(p)
        return {}
    return ui.info("Лог появится после запуска прокси.")


def _copy(app, _):
    url = tgproxy.proxy_link(app.core.tg.load_config())
    app.clipboard_set(url)
    return ui.done("Ссылка скопирована")


def _secret_new(app, _):
    draft = dict(app.page_state["tg"].get("draft") or app.core.tg.load_config())
    draft["secret"] = tgproxy.new_secret()
    app.page_state["tg"]["draft"] = draft
    return ui.done("Новый secret — нажмите «Сохранить»", clear=["secret"])


def _phone(app, _):
    app.sheet = PhoneSheet(app)


ACTIONS = {"save": _save, "defaults": _defaults, "revert": _revert, "restart": _restart, "log": _log,
           "copy": _copy, "secret.new": _secret_new, "phone": _phone, "tg.open": _tg_open}
