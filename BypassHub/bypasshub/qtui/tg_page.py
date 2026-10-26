"""TG WS Proxy — все настройки из окна «Настройки» tg-ws-proxy."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from .. import tgproxy, winutil
from ..tgproxy import DEFAULT_CONFIG
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from .app import App

APPEARANCE = {"auto": "Как в системе", "light": "Светлая", "dark": "Тёмная"}
LANGUAGES = {"ru": "Русский", "en": "English"}


def _s(value) -> str:
    return "" if value is None else str(value)


class TgPage(Page):
    def __init__(self, app: "App"):
        super().__init__(app)
        from .home import ServiceCard
        home = app.pages["home"][1]
        self.header = ServiceCard("TG WS Proxy", "Локальный MTProto-прокси, ускоряющий Telegram Desktop",
                                  home.toggle_tg)
        self.header.add_buttons(W.button("Открыть в Telegram", home.open_in_telegram, primary=True),
                                W.button("Скопировать ссылку", home.copy_link),
                                W.button("Подключить телефон", app.open_phone_dialog))
        self.lay.addWidget(self.header)
        home.tg_card.add_header(self.header)

        body = W.section(self.lay, "Подключение MTProto",
                         "Адрес, на котором прокси принимает подключения Telegram Desktop. "
                         "Для доступа из локальной сети укажите 0.0.0.0.")
        self.host = W.row(body, "IP-адрес", W.entry())
        self.port = W.row(body, "Порт", W.entry())
        self.secret = W.entry(mono=True)
        new = W.button("Новый", lambda: self.secret.setText(tgproxy.new_secret()), primary=True)
        lbl = W.label("Secret")
        lbl.setFixedWidth(230)
        body.addLayout(W.hbox(lbl, self.secret, new, stretch_last=False))

        body = W.section(self.lay, "Датацентры Telegram (DC → IP)",
                         "По одному правилу на строку, формат: номер:IP. Если не грузятся фото/видео — "
                         "оставьте только 4:149.154.167.220 или очистите поле.")
        self.dc_ip = W.textbox(90)
        body.addWidget(self.dc_ip)

        body = W.section(self.lay, "Cloudflare Proxy",
                         "Запасной путь через Cloudflare, если прямое WebSocket-подключение недоступно.")
        self.cfproxy = W.Toggle("Включить CF-прокси")
        self.h2 = W.Toggle("Мультиплексирование медиа (HTTP/2)")
        self.user_domain_enabled = W.Toggle("Свой домен")
        for t in (self.cfproxy, self.h2, self.user_domain_enabled):
            body.addWidget(t)
        self.user_domain = W.row(body, "Домены (через запятую)", W.entry(),
                                 hint="Инструкция по настройке домена: docs/RU/CfProxy.md в репозитории tg-ws-proxy")

        body = W.section(self.lay, "Cloudflare Worker", "Бесплатный аналог своего CF-домена (docs/RU/CfWorker.md).")
        self.worker_enabled = W.Toggle("Использовать Cloudflare Worker")
        body.addWidget(self.worker_enabled)
        self.worker_domain = W.row(body, "Worker-домены (через запятую)", W.entry())

        body = W.section(self.lay, "Логи и производительность")
        self.verbose = W.Toggle("Подробное логирование (verbose)")
        self.no_secure = W.Toggle("Выключить TLS для CF-прокси и CF-worker")
        self.force_test_dc = W.Toggle("Тестовые датацентры Telegram (только для разработчиков)")
        for t in (self.verbose, self.no_secure, self.force_test_dc):
            body.addWidget(t)
        self.buf_kb = W.row(body, "Буфер, КБ (по умолчанию 256)", W.entry())
        self.pool_size = W.row(body, "Пул WebSocket-сессий (по умолчанию 4)", W.entry())
        self.log_max_mb = W.row(body, "Макс. размер лога, МБ (по умолчанию 5)", W.entry())

        body = W.section(self.lay, "Интерфейс tg-ws-proxy",
                         "Оформление собственных окон прокси (их почти не видно — всё управление здесь).")
        self.appearance = W.row(body, "Тема", W.Segmented(APPEARANCE.values()))
        self.language = W.row(body, "Язык", W.Segmented(LANGUAGES.values()))

        self.lay.addLayout(W.button_bar(W.button("Сохранить", self.save, primary=True),
                                        W.button("Отменить изменения", self.reload),
                                        W.button("По умолчанию", self.reset_defaults)))
        self.lay.addLayout(W.button_bar(W.button("Лог прокси", self.open_log),
                                        W.button("Перезапустить", self.restart)))
        self.finish()
        self.reload()

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        pass  # не перезаписываем несохранённые правки при переключении вкладок

    def _fill(self, cfg: Dict[str, Any]) -> None:
        self.host.setText(_s(cfg.get("host")))
        self.port.setText(_s(cfg.get("port")))
        self.secret.setText(_s(cfg.get("secret")))
        self.dc_ip.setPlainText("\n".join(cfg.get("dc_ip") or []))
        W.set_checked(self.cfproxy, cfg.get("cfproxy", True))
        W.set_checked(self.h2, cfg.get("h2", True))
        W.set_checked(self.user_domain_enabled, cfg.get("cfproxy_user_domain_enabled", False))
        self.user_domain.setText(", ".join(tgproxy.coerce_domain_list(cfg.get("cfproxy_user_domain"))))
        W.set_checked(self.worker_enabled, cfg.get("cfproxy_worker_enabled", False))
        self.worker_domain.setText(", ".join(tgproxy.coerce_domain_list(cfg.get("cfproxy_worker_domain"))))
        W.set_checked(self.verbose, cfg.get("verbose", False))
        W.set_checked(self.no_secure, cfg.get("no_secure", False))
        W.set_checked(self.force_test_dc, cfg.get("force_test_dc", False))
        self.buf_kb.setText(_s(cfg.get("buf_kb")))
        self.pool_size.setText(_s(cfg.get("pool_size")))
        self.log_max_mb.setText(_s(cfg.get("log_max_mb")))
        self.appearance.set(APPEARANCE.get(cfg.get("appearance", "auto"), APPEARANCE["auto"]))
        self.language.set(LANGUAGES.get(cfg.get("language", "ru"), LANGUAGES["ru"]))

    def reload(self) -> None:
        self._fill(self.app.core.tg.load_config())

    def reset_defaults(self) -> None:
        if not self.app.ask("Вернуть настройки прокси по умолчанию? Secret сохранится."):
            return
        cfg = dict(DEFAULT_CONFIG)
        cfg["secret"] = self.secret.text().strip() or tgproxy.new_secret()
        self._fill(cfg)

    def _collect(self) -> Dict[str, Any]:
        cfg = self.app.core.tg.load_config()
        cfg.update({
            "host": self.host.text(),
            "port": self.port.text(),
            "secret": self.secret.text(),
            "dc_ip": self.dc_ip.toPlainText(),
            "cfproxy": self.cfproxy.isChecked(),
            "h2": self.h2.isChecked(),
            "cfproxy_user_domain_enabled": self.user_domain_enabled.isChecked(),
            "cfproxy_user_domain": self.user_domain.text(),
            "cfproxy_worker_enabled": self.worker_enabled.isChecked(),
            "cfproxy_worker_domain": self.worker_domain.text(),
            "verbose": self.verbose.isChecked(),
            "no_secure": self.no_secure.isChecked(),
            "force_test_dc": self.force_test_dc.isChecked(),
            "buf_kb": self.buf_kb.text(),
            "pool_size": self.pool_size.text(),
            "log_max_mb": self.log_max_mb.text(),
            "appearance": next((k for k, v in APPEARANCE.items() if v == self.appearance.get()), "auto"),
            "language": next((k for k, v in LANGUAGES.items() if v == self.language.get()), "ru"),
        })
        return cfg

    def save(self) -> None:
        result = tgproxy.validate(self._collect())
        if isinstance(result, str):
            self.app.error(result, "Настройки прокси")
            return
        if result["cfproxy_user_domain_enabled"] and not result["cfproxy_user_domain"]:
            self.app.error("Включён «Свой домен», но домен не указан.", "Настройки прокси")
            return
        if result["cfproxy_worker_enabled"] and not result["cfproxy_worker_domain"]:
            self.app.error("Включён Cloudflare Worker, но домен не указан.", "Настройки прокси")
            return
        self.app.core.tg.save_config(result)
        self._fill(result)
        try:  # адрес 0.0.0.0 — нужен доступ из сети: правило брандмауэра добавляется/удаляется само
            self.app.core.tg.sync_firewall(result)
        except Exception as exc:
            self.app.error(str(exc), "Брандмауэр")

        def done(restarted, err):
            if err:
                self.app.error(str(err), "TG WS Proxy")
            else:
                self.app.info("Настройки сохранены" + (", прокси перезапущен." if restarted else "."))
        self.app.run_task(self.app.core.tg_apply, done)

    def restart(self) -> None:
        def work():
            self.app.core.settings.set("tg", "enabled", True)
            self.app.core.tg.restart()
        self.app.run_task(work, lambda r, e: self.app.error(str(e)) if e else None)

    def open_log(self) -> None:
        p = self.app.core.tg.log_file
        if p.exists():
            winutil.open_path(p)
        else:
            self.app.info("Лог появится после запуска прокси.")
