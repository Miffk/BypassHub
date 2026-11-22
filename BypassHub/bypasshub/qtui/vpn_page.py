"""VPN: свои серверы — ключи VLESS (sing-box) и AmneziaWG (ключи AmneziaVPN vpn://)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple

from PySide6.QtWidgets import QFileDialog, QInputDialog, QVBoxLayout, QWidget

from .. import vpn, winutil
from ..vpn import Profile
from . import widgets as W
from .app import Page

if TYPE_CHECKING:
    from ..core import VpnState
    from .app import App


def _when(ts: float) -> str:
    now, t = time.localtime(), time.localtime(ts)
    if t[:3] == now[:3]:
        return "в " + time.strftime("%H:%M", t)
    return time.strftime("%d.%m в %H:%M", t)


def _rules_age(manager) -> str:
    files = [p for p in manager.rule_set_paths().values() if p.exists()]
    if not files:
        return ""
    return "от " + time.strftime("%d.%m.%Y", time.localtime(max(p.stat().st_mtime for p in files)))


class VpnPage(Page):
    def __init__(self, app: "App"):
        super().__init__(app)
        s = app.core.settings
        from .home import ServiceCard
        home = app.pages["home"][1]
        self.header = ServiceCard("VPN", "Свой сервер: ключ VLESS или ключ AmneziaVPN (AmneziaWG)", home.toggle_vpn)
        self.header.add_buttons()
        self.lay.addWidget(self.header)
        home.vpn_card.add_header(self.header)

        body = W.section(self.lay, "Добавить сервер",
                         "Вставьте ключ vless://… или hysteria2://…, ключ AmneziaVPN vpn://… (AmneziaVPN → «Поделиться»), "
                         "конфиг AmneziaWG (.conf) или ссылку на подписку https://… от вашего VPN-сервиса. "
                         "Можно несколько ключей сразу — по одному на строку.")
        self.input = W.textbox(74)
        body.addWidget(self.input)
        self.add_btn = W.button("Добавить", self.add_from_box, primary=True, width=120)
        body.addLayout(W.button_bar(self.add_btn, W.button("Вставить из буфера", self.add_from_clipboard),
                                    W.button("Из файлов .conf…", self.add_from_file)))
        body.addWidget(W.label("Ключ — это пароль от вашего сервера: не публикуйте его и не отправляйте посторонним.",
                               "small", wrap=True))

        body = W.section(self.lay, "Серверы", "Нажмите на сервер, чтобы выбрать его. Если VPN включён, он "
                                              "переключится сразу. «Авто» сам выбирает самый быстрый сервер VLESS или Hysteria2.")
        from .server_list import ServerPicker
        self.picker = ServerPicker(app, compact=False, on_rename=self.rename, on_remove=self.remove)
        body.addWidget(self.picker)
        home.pickers.append(self.picker)
        self.subs_box = QVBoxLayout()
        self.subs_box.setSpacing(2)
        body.addLayout(self.subs_box)
        self.ping_btn = W.button("Проверить пинг", lambda: home.ping_servers(), width=170)
        self.subs_btn = W.button("⟳ Обновить подписки", self.refresh_subs, width=190)
        body.addLayout(W.button_bar(self.ping_btn, self.subs_btn))

        body = W.section(self.lay, "Режим",
                         "«Весь трафик» — через VPN идут все программы, включая игры и Discord (виртуальный "
                         "сетевой адаптер). «Системный прокси» — только браузеры и программы, которые "
                         + ("учитывают системные настройки прокси (KDE Plasma, GNOME); остальные работают как "
                            "обычно. Для режима «Весь трафик» система спросит пароль администратора."
                            if winutil.IS_LINUX else
                            "учитывают настройки прокси Windows; остальные работают как обычно."))
        self.mode = W.row(body, "Что пускать через VPN", W.Segmented(vpn.MODES.values(), self._mode_changed))
        self.ru_direct = W.Toggle("Раздельное туннелирование: российское — напрямую", self._ru_changed)
        body.addWidget(self.ru_direct)
        body.addWidget(W.label("Через VPN идёт всё зарубежное. Напрямую, с домашнего IP, — сайты .ru, .рф, .su со всеми "
                               "поддоменами, российские IP-адреса и российские сервисы на «нерусских» доменах (VK, "
                               "Яндекс, банки и др.) по базам roscomvpn. YouTube, Telegram, GitHub и Google Play всегда "
                               "идут через VPN. Российский трафик не попадает на сервер вовсе.", "small", wrap=True))
        self.port = W.entry()
        self.port.setFixedWidth(100)
        port_label = W.label("Порт системного прокси")
        port_label.setFixedWidth(230)
        body.addLayout(W.hbox(port_label, self.port, W.button("Применить", self._port_changed, primary=True)))

        body = W.section(self.lay, "Программы мимо VPN",
                         "Отмеченные программы ходят в интернет напрямую, даже когда VPN включён, — например, "
                         "игры, в которых через VPN растёт пинг. Работает в режиме «Весь трафик»: в режиме "
                         "«Системный прокси» игры и так идут мимо VPN.")
        self.exclude_grid = W.grid(2)
        self.exclude_checks = {}
        body.addLayout(self.exclude_grid)
        self.exclude_entry = W.entry("game, other" if winutil.IS_LINUX else "game.exe, other.exe")
        self.exclude_entry.returnPressed.connect(self._exclude_add)
        ex_label = W.label("Добавить программу")
        ex_label.setFixedWidth(170)
        body.addLayout(W.hbox(ex_label, self.exclude_entry, W.button("Выбрать…", self._exclude_pick),
                              W.button("Добавить", self._exclude_add, primary=True), stretch_last=False))
        body.addWidget(W.label("Впишите имя файла программы (можно несколько через запятую) или нажмите «Выбрать…» "
                               "и укажите файл игры в папке, куда она установлена. Программа появится в списке "
                               "выше с галочкой; снимите галочку, чтобы она снова шла через VPN, или нажмите ✕, "
                               "чтобы убрать её из списка." if winutil.IS_LINUX else
                               "Впишите имя .exe (можно несколько через запятую) или нажмите «Выбрать…» и укажите "
                               ".exe игры в папке, куда она установлена. Программа появится в списке выше с "
                               "галочкой; снимите галочку, чтобы она снова шла через VPN, или нажмите ✕, чтобы "
                               "убрать её из списка.", "small", wrap=True))

        body = W.section(self.lay, "AmneziaWG",
                         "Перед включением отключитесь в приложении AmneziaVPN, если оно открыто, — два VPN "
                         "одновременно мешают друг другу. Раздельное туннелирование и режим «Системный прокси» "
                         + ("для AmneziaWG работают через sing-box. В Linux AmneziaWG встроен в BypassHub и "
                            "работает без модуля ядра — ставить ничего не нужно."
                            if winutil.IS_LINUX else
                            "для AmneziaWG работают через sing-box — он скачается вместе с клиентом AmneziaWG."))
        self.lan_access = W.Toggle("Без раздельного туннелирования: оставить доступ к локальной сети",
                                   self._lan_changed)
        body.addWidget(self.lan_access)
        body.addWidget(W.label("Принтеры, телефон для TG WS Proxy и т. п. Если выключить, интернет мимо VPN блокируется "
                               "полностью (kill switch). При раздельном туннелировании локальная сеть доступна всегда.",
                               "small", wrap=True))

        body = W.section(self.lay, "Движки VPN", "Скачиваются с GitHub при первом включении и обновляются "
                                                 "вместе с остальными компонентами.")
        self.engines = W.label("", wrap=True)
        body.addWidget(self.engines)
        body.addLayout(W.button_bar(W.button("Лог VPN", self.open_log),
                                    W.button("Перезапустить VPN", self.restart)))
        self.finish()

        self.mode.set(vpn.MODES.get(s.get("vpn", "mode"), vpn.MODES["tun"]))
        W.set_checked(self.ru_direct, s.get("vpn", "ru_direct"))
        W.set_checked(self.lan_access, s.get("vpn", "lan_access"))
        self.port.setText(str(s.get("vpn", "proxy_port")))
        self._exclude_load()
        self.render()

    # ------------------------------------------------------------------ список серверов
    def on_show(self) -> None:
        self.render()
        home = self.app.pages["home"][1]
        home.ping_servers(max_age=home.PING_MAX_AGE)

    def on_palette(self) -> None:
        self.picker.render(force=True)
        self.render()

    def render(self) -> None:
        core = self.app.core
        W.clear_layout(self.subs_box)
        self.picker.render()
        subs = core.settings.get("vpn", "subscriptions") or []
        if subs:
            self.subs_box.addLayout(W.hbox(W.label("Подписки", "h2"),
                                           W.label("обновляются сами при запуске и каждые несколько часов", "small"),
                                           margins=(0, 10, 0, 2)))
        for url in subs:
            shown = url if len(url) <= 60 else url[:57] + "…"
            checked = core.vpn_sub_checked(url)
            when = W.label(("обновлена " + _when(checked)) if checked else "", "small")
            self.subs_box.addLayout(W.hbox(W.label(shown, "muted"), None, when,
                                           W.button("✕", lambda u=url: self.remove_sub(u), flat=True),
                                           stretch_last=False))
        self.subs_btn.setEnabled(bool(subs))
        if not self.app.pages["home"][1]._pinging:
            self.ping_btn.setEnabled(bool(core.vpn_vless()))
        sb, aw = core.singbox, core.awg
        self.engines.setText("\n".join([
            "VLESS — sing-box " + (sb.local_version() if sb.is_installed() else "(скачается с GitHub при первом включении)"),
            "AmneziaWG — официальный клиент AmneziaWG " + (aw.local_version() if aw.is_installed() else
                                                         "(скачается с GitHub при первом включении)"),
            "Раздельное туннелирование — базы roscomvpn " + (_rules_age(sb) or "(скачаются при включении)"),
        ]))

    def rename(self, p: Profile) -> None:
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое название сервера:", text=p.name)
        if ok and name.strip():
            self.app.core.vpn_rename(p.id, name)
            self.render()
            self.app.pages["home"][1].reload_vpn()

    def remove(self, p: Profile) -> None:
        if not self.app.ask(f"Удалить сервер «{p.name}»?"):
            return
        core = self.app.core
        was_active = core.vpn_active()
        core.vpn_remove(p.id)
        self.render()
        home = self.app.pages["home"][1]
        home.reload_vpn()
        if self.app.vpn_state.running:
            if not core.vpn_profiles():
                home.toggle_vpn(False)
            elif (was_active is not None and was_active.id == p.id) or p.kind == "vless":
                home.apply_vpn("Переключение сервера…")  # серверы VLESS собраны в одну группу

    def remove_sub(self, url: str) -> None:
        if not self.app.ask("Удалить подписку и все её серверы?\n" + url):
            return
        self.app.core.vpn_remove_subscription(url)
        self.render()
        self.app.pages["home"][1].reload_vpn()

    # ------------------------------------------------------------------ добавление
    def add_from_box(self) -> None:
        self._add(self.input.toPlainText(), clear=True)

    def add_from_clipboard(self) -> None:
        text = self.app.clipboard_text()
        if not text.strip():
            self.app.info("Буфер обмена пуст — скопируйте ключ и нажмите ещё раз.")
            return
        self._add(text)

    def add_from_file(self) -> None:
        # можно выбрать сразу несколько файлов — например, по файлу на каждую страну из AmneziaVPN
        paths, _ = QFileDialog.getOpenFileNames(self, "Конфиги AmneziaWG", "",
                                                "Конфиг AmneziaWG / WireGuard (*.conf);;Все файлы (*.*)")
        if not paths:
            return
        files = []
        for path in paths:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    files.append((f.read(1024 * 1024), Path(path).stem))  # имя сервера — по имени файла
            except OSError as exc:
                self.app.error(f"{Path(path).name}: {exc}", "Конфиг")
                return
        self._add_many(files)

    def _add(self, text: str, clear: bool = False, name: str = "") -> None:
        if not text.strip():
            self.app.info("Вставьте ключ в поле выше.")
            return
        self._add_many([(text, name)], clear)

    def _add_many(self, items, clear: bool = False) -> None:
        """items — [(текст, имя)]: имя даётся серверу AmneziaWG из одного конфига (по имени файла)."""
        self.add_btn.setEnabled(False)
        core = self.app.core

        def work():
            added, problems = [], []
            for text, name in items:
                got, extra = core.vpn_add(text)
                if name and len(got) == 1 and got[0].kind == "awg":
                    core.vpn_rename(got[0].id, name)
                    got[0].name = name
                added += got
                problems += [f"{name}: {x}" if name and len(items) > 1 else x for x in extra]
            return added, problems

        def done(result, err):
            self.add_btn.setEnabled(True)
            if err:
                self.app.error(str(err), "VPN")
                return
            added, problems = result
            if clear and added:
                self.input.clear()
            self.render()
            self.app.pages["home"][1].reload_vpn()
            msg = []
            if added:
                msg.append("Добавлено серверов: " + str(len(added)) + "\n" +
                           "\n".join("• " + p.name for p in added[:10]) + ("\n…" if len(added) > 10 else ""))
            elif not problems:
                msg.append("Эти серверы уже есть в списке.")
            if problems:
                msg.append("Не добавлено:\n" + "\n".join("• " + x for x in problems[:8]))
            (self.app.info if added else self.app.error)("\n\n".join(msg), "VPN")
        self.app.run_task(work, done)

    def refresh_subs(self) -> None:
        self.subs_btn.setEnabled(False)
        self.subs_btn.setText("Обновляю…")

        def done(result, err):
            self.subs_btn.setEnabled(True)
            self.subs_btn.setText("⟳ Обновить подписки")
            if err:
                self.app.error(str(err), "Подписки")
                return
            total, problems = result
            self.app.subscriptions_changed()
            text = f"Серверов в подписках: {total}"
            if problems:
                text += "\n\n" + "\n".join("• " + x for x in problems[:8])
            self.app.info(text, "Подписки")
        self.app.run_task(self.app.core.vpn_refresh_subscriptions, done)

    # ------------------------------------------------------------------ режимы
    def _mode_changed(self, text: str) -> None:
        mode = next((k for k, v in vpn.MODES.items() if v == text), "tun")
        self.app.core.settings.set("vpn", "mode", mode)
        self.app.pages["home"][1].apply_vpn("Смена режима…")

    def _ru_changed(self, on: bool) -> None:
        self.app.core.settings.set("vpn", "ru_direct", on)
        self.app.pages["home"][1].apply_vpn("Применение настроек…")

    def _lan_changed(self, on: bool) -> None:
        self.app.core.settings.set("vpn", "lan_access", on)
        vs = self.app.vpn_state
        if vs.kind == "awg" and not (vs.singbox and vs.singbox.running):
            self.app.pages["home"][1].apply_vpn("Применение настроек…")

    def _port_changed(self) -> None:
        try:
            port = int(self.port.text().strip())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.app.error("Порт — число от 1024 до 65535", "VPN")
            return
        self.app.core.settings.set("vpn", "proxy_port", port)
        if self.app.vpn_state.mode == "proxy":
            self.app.pages["home"][1].apply_vpn("Применение настроек…")

    def _exclude_state(self) -> Tuple[List[str], List[str]]:
        """(отмеченные программы, свои программы в списке). Свои — и те, что вписаны раньше, до списка с
        галочками (тогда они хранились только среди отмеченных)."""
        s = self.app.core.settings
        names = vpn.clean_exe_names(s.get("vpn", "exclude_apps") or [])
        in_presets = {e for exes in vpn.EXCLUDE_PRESETS.values() for e in exes}
        custom = vpn.clean_exe_names((s.get("vpn", "exclude_custom") or [])
                                     + [n for n in names if n not in in_presets])
        return names, [c for c in custom if c not in in_presets]

    def _exclude_load(self) -> None:
        names, custom = self._exclude_state()
        W.clear_layout(self.exclude_grid)
        self.exclude_checks = {}
        items = [(title, exes) for title, exes in vpn.EXCLUDE_PRESETS.items()] + [(c, [c]) for c in custom]
        for i, (title, exes) in enumerate(items):
            cb = W.checkbox(title)
            W.set_checked(cb, all(e in names for e in exes))
            cb.toggled.connect(lambda _on: self._exclude_changed())
            if title in vpn.EXCLUDE_PRESETS:
                cell = cb
            else:
                remove = W.button("✕", lambda n=title: self._exclude_remove(n), flat=True)
                remove.setToolTip("Убрать из списка")
                remove.setFixedWidth(28)
                cell = QWidget()
                cell.setLayout(W.hbox(cb, remove, spacing=2))
            self.exclude_grid.addWidget(cell, i // 2, i % 2)
            self.exclude_checks[title] = (cb, exes)

    def _exclude_save(self, names: List[str], custom: List[str]) -> None:
        s = self.app.core.settings
        names, custom = vpn.clean_exe_names(names), vpn.clean_exe_names(custom)
        changed = names != vpn.clean_exe_names(s.get("vpn", "exclude_apps") or [])
        rebuild = custom != self._exclude_state()[1]
        s.set("vpn", "exclude_custom", custom, save=False)
        s.set("vpn", "exclude_apps", names)
        if rebuild:  # новые или убранные программы; при щелчке по галочке список не пересобираем
            self._exclude_load()
        if changed and s.get("vpn", "mode") == "tun":
            self.app.pages["home"][1].apply_vpn("Применение настроек…")

    def _exclude_changed(self) -> None:
        names: List[str] = []
        for cb, exes in self.exclude_checks.values():
            if cb.isChecked():
                names += exes
        self._exclude_save(names, self._exclude_state()[1])

    def _exclude_add(self) -> None:
        new = vpn.clean_exe_names([x for x in re.split(r"[,;\n]", self.exclude_entry.text()) if x.strip()])
        if not new:
            return
        self.exclude_entry.clear()
        names, custom = self._exclude_state()
        self._exclude_save(names + new, custom + new)

    def _exclude_remove(self, name: str) -> None:
        names, custom = self._exclude_state()
        self._exclude_save([n for n in names if n != name], [c for c in custom if c != name])

    def _exclude_pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Программа мимо VPN", "",
                                              "Все файлы (*)" if winutil.IS_LINUX
                                              else "Программы (*.exe);;Все файлы (*.*)")
        if not path:
            return
        self.exclude_entry.setText(vpn.clean_exe_names([path])[0])
        self._exclude_add()

    def restart(self) -> None:
        self.app.pages["home"][1].toggle_vpn(True)

    def open_log(self) -> None:
        p = self.app.core.paths.vpn_log
        if self.app.vpn_state.kind == "awg" or (not p.exists() and self.app.core.awg.is_installed()):
            self.app.info("Журнал AmneziaWG ведёт сама служба туннеля. Ошибки подключения BypassHub пишет в "
                          "свой журнал (вкладка «Журнал»).")
            return
        if p.exists():
            winutil.open_path(p)
        else:
            self.app.info("Лог появится после первого включения VPN.")

    def on_vpn_status(self, vs: "VpnState") -> None:
        pass
