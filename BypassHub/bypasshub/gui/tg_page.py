from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

import customtkinter as ctk

from .. import tgproxy, winutil
from ..tgproxy import DEFAULT_CONFIG
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

APPEARANCE = {"auto": "Как в системе", "light": "Светлая", "dark": "Тёмная"}
LANGUAGES = {"ru": "Русский", "en": "English"}


class TgPage(ctk.CTkScrollableFrame):
    """Все настройки из окна «Настройки» tg-ws-proxy."""

    def __init__(self, parent, app: "App"):
        super().__init__(parent, fg_color=W.P.window_bg, scrollbar_button_color=W.P.border)
        self.app = app
        W.page_title(self, "TG WS Proxy", "Все настройки локального MTProto-прокси для Telegram Desktop")

        body = W.section(self, "Подключение MTProto",
                         "Адрес, на котором прокси принимает подключения Telegram Desktop. "
                         "Для доступа из локальной сети укажите 0.0.0.0.")
        self.host = W.row(body, "IP-адрес", lambda p: ctk.CTkEntry(p))
        self.port = W.row(body, "Порт", lambda p: ctk.CTkEntry(p))
        fr = ctk.CTkFrame(body, fg_color="transparent")
        fr.pack(fill="x", pady=3)
        ctk.CTkLabel(fr, text="Secret", width=230, anchor="w").pack(side="left")
        self.secret = ctk.CTkEntry(fr, font=ctk.CTkFont(family="Consolas", size=12))
        self.secret.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(fr, text="Новый", width=80, command=lambda: W.set_entry(self.secret, tgproxy.new_secret())
                      ).pack(side="left", padx=(8, 0))

        body = W.section(self, "Датацентры Telegram (DC → IP)",
                         "По одному правилу на строку, формат: номер:IP. Если не грузятся фото/видео — "
                         "оставьте только 4:149.154.167.220 или очистите поле.")
        self.dc_ip = ctk.CTkTextbox(body, height=90, font=ctk.CTkFont(family="Consolas", size=12))
        self.dc_ip.pack(fill="x")

        body = W.section(self, "Cloudflare Proxy",
                         "Запасной путь через Cloudflare, если прямое WebSocket-подключение недоступно.")
        self.cfproxy = ctk.CTkSwitch(body, text="Включить CF-прокси")
        self.cfproxy.pack(anchor="w", pady=3)
        self.h2 = ctk.CTkSwitch(body, text="Мультиплексирование медиа (HTTP/2)")
        self.h2.pack(anchor="w", pady=3)
        self.user_domain_enabled = ctk.CTkSwitch(body, text="Свой домен")
        self.user_domain_enabled.pack(anchor="w", pady=3)
        self.user_domain = W.row(body, "Домены (через запятую)", lambda p: ctk.CTkEntry(p),
                                 hint="Инструкция по настройке домена: docs/RU/CfProxy.md в репозитории tg-ws-proxy")

        body = W.section(self, "Cloudflare Worker", "Бесплатный аналог своего CF-домена (docs/RU/CfWorker.md).")
        self.worker_enabled = ctk.CTkSwitch(body, text="Использовать Cloudflare Worker")
        self.worker_enabled.pack(anchor="w", pady=3)
        self.worker_domain = W.row(body, "Worker-домены (через запятую)", lambda p: ctk.CTkEntry(p))

        body = W.section(self, "Логи и производительность")
        self.verbose = ctk.CTkSwitch(body, text="Подробное логирование (verbose)")
        self.verbose.pack(anchor="w", pady=3)
        self.no_secure = ctk.CTkSwitch(body, text="Выключить TLS для CF-прокси и CF-worker")
        self.no_secure.pack(anchor="w", pady=3)
        self.force_test_dc = ctk.CTkSwitch(body, text="Тестовые датацентры Telegram (только для разработчиков)")
        self.force_test_dc.pack(anchor="w", pady=3)
        self.buf_kb = W.row(body, "Буфер, КБ (по умолчанию 256)", lambda p: ctk.CTkEntry(p))
        self.pool_size = W.row(body, "Пул WebSocket-сессий (по умолчанию 4)", lambda p: ctk.CTkEntry(p))
        self.log_max_mb = W.row(body, "Макс. размер лога, МБ (по умолчанию 5)", lambda p: ctk.CTkEntry(p))

        body = W.section(self, "Интерфейс tg-ws-proxy",
                         "Оформление собственных окон прокси (их почти не видно — всё управление здесь).")
        self.appearance = W.row(body, "Тема", lambda p: ctk.CTkSegmentedButton(p, values=list(APPEARANCE.values())))
        self.language = W.row(body, "Язык", lambda p: ctk.CTkSegmentedButton(p, values=list(LANGUAGES.values())))

        bar = W.button_bar(self)
        W.add_button(bar, "Сохранить", self.save)
        W.add_button(bar, "Отменить изменения", self.reload, secondary=True, width=170)
        W.add_button(bar, "По умолчанию", self.reset_defaults, secondary=True)
        bar = W.button_bar(self)
        home = lambda: app.pages["home"][1]  # noqa: E731
        W.add_button(bar, "Открыть в Telegram", lambda: home().open_in_telegram(), width=170)
        W.add_button(bar, "Скопировать ссылку", lambda: home().copy_link(), secondary=True, width=170)
        W.add_button(bar, "Лог прокси", self.open_log, secondary=True)
        W.add_button(bar, "Перезапустить", self.restart, secondary=True)

        self.reload()

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        pass  # не перезаписываем несохранённые правки при переключении вкладок

    def _fill(self, cfg: Dict[str, Any]) -> None:
        W.set_entry(self.host, cfg.get("host"))
        W.set_entry(self.port, cfg.get("port"))
        W.set_entry(self.secret, cfg.get("secret"))
        W.set_text(self.dc_ip, "\n".join(cfg.get("dc_ip") or []))
        W.set_switch(self.cfproxy, cfg.get("cfproxy", True))
        W.set_switch(self.h2, cfg.get("h2", True))
        W.set_switch(self.user_domain_enabled, cfg.get("cfproxy_user_domain_enabled", False))
        W.set_entry(self.user_domain, ", ".join(tgproxy.coerce_domain_list(cfg.get("cfproxy_user_domain"))))
        W.set_switch(self.worker_enabled, cfg.get("cfproxy_worker_enabled", False))
        W.set_entry(self.worker_domain, ", ".join(tgproxy.coerce_domain_list(cfg.get("cfproxy_worker_domain"))))
        W.set_switch(self.verbose, cfg.get("verbose", False))
        W.set_switch(self.no_secure, cfg.get("no_secure", False))
        W.set_switch(self.force_test_dc, cfg.get("force_test_dc", False))
        W.set_entry(self.buf_kb, cfg.get("buf_kb"))
        W.set_entry(self.pool_size, cfg.get("pool_size"))
        W.set_entry(self.log_max_mb, cfg.get("log_max_mb"))
        self.appearance.set(APPEARANCE.get(cfg.get("appearance", "auto"), APPEARANCE["auto"]))
        self.language.set(LANGUAGES.get(cfg.get("language", "ru"), LANGUAGES["ru"]))

    def reload(self) -> None:
        self._fill(self.app.core.tg.load_config())

    def reset_defaults(self) -> None:
        if not self.app.ask("Вернуть настройки прокси по умолчанию? Secret сохранится."):
            return
        cfg = dict(DEFAULT_CONFIG)
        cfg["secret"] = self.secret.get().strip() or tgproxy.new_secret()
        self._fill(cfg)

    def _collect(self) -> Dict[str, Any]:
        cfg = self.app.core.tg.load_config()
        cfg.update({
            "host": self.host.get(),
            "port": self.port.get(),
            "secret": self.secret.get(),
            "dc_ip": self.dc_ip.get("1.0", "end-1c"),
            "cfproxy": bool(self.cfproxy.get()),
            "h2": bool(self.h2.get()),
            "cfproxy_user_domain_enabled": bool(self.user_domain_enabled.get()),
            "cfproxy_user_domain": self.user_domain.get(),
            "cfproxy_worker_enabled": bool(self.worker_enabled.get()),
            "cfproxy_worker_domain": self.worker_domain.get(),
            "verbose": bool(self.verbose.get()),
            "no_secure": bool(self.no_secure.get()),
            "force_test_dc": bool(self.force_test_dc.get()),
            "buf_kb": self.buf_kb.get(),
            "pool_size": self.pool_size.get(),
            "log_max_mb": self.log_max_mb.get(),
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
