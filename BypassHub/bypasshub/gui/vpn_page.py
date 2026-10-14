from __future__ import annotations

from tkinter import filedialog
from typing import TYPE_CHECKING, Dict, List, Optional

import customtkinter as ctk

from .. import vpn, winutil
from ..vpn import Profile
from . import widgets as W
from .surface import GradientPage

if TYPE_CHECKING:
    from ..core import VpnState
    from .app import App

KIND_LABELS = {"vless": "VLESS", "awg": "AmneziaWG"}


class VpnPage(GradientPage):
    """Свои VPN-серверы: ключи VLESS (sing-box) и AmneziaWG (ключи AmneziaVPN vpn://)."""

    def __init__(self, parent, app: "App"):
        super().__init__(parent)
        self.app = app
        s = app.core.settings
        self._pings: Dict[str, Optional[float]] = {}
        W.page_title(self, "VPN", "Свой VPN-сервер: ключ VLESS или ключ AmneziaVPN (AmneziaWG)")

        body = W.section(self, "Добавить сервер",
                         "Вставьте ключ vless://…, ключ AmneziaVPN vpn://… (AmneziaVPN → «Поделиться»), "
                         "конфиг AmneziaWG (.conf) или ссылку на подписку https://… от вашего VPN-сервиса. "
                         "Можно несколько ключей сразу — по одному на строку.")
        self.input = ctk.CTkTextbox(body, height=74, font=ctk.CTkFont(family="Consolas", size=12))
        self.input.pack(fill="x")
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))
        self.add_btn = W.add_button(bar, "Добавить", self.add_from_box, width=120)
        W.add_button(bar, "Вставить из буфера", self.add_from_clipboard, secondary=True, width=170)
        W.add_button(bar, "Из файла .conf…", self.add_from_file, secondary=True, width=150)
        ctk.CTkLabel(body, text="Ключ — это пароль от вашего сервера: не публикуйте его и не отправляйте "
                                "посторонним.", text_color=W.GRAY, anchor="w", justify="left", wraplength=640,
                     font=ctk.CTkFont(size=11)).pack(fill="x", pady=(6, 0))

        body = W.section(self, "Серверы", "Нажмите на сервер, чтобы выбрать его. Если VPN включён, "
                                          "он сразу переключится.")
        # height=1: пустой CTkFrame иначе занимает 200 px
        self.list_frame = ctk.CTkFrame(body, fg_color="transparent", height=1)
        self.list_frame.pack(fill="x")
        self.subs_frame = ctk.CTkFrame(body, fg_color="transparent", height=1)
        self.subs_frame.pack(fill="x")
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))
        self.ping_btn = W.add_button(bar, "Проверить серверы", self.ping_all, secondary=True, width=170)
        self.subs_btn = W.add_button(bar, "Обновить подписки", self.refresh_subs, secondary=True, width=170)

        body = W.section(self, "Режим",
                         "«Весь трафик» — через VPN идут все программы, включая игры и Discord (виртуальный "
                         "сетевой адаптер). «Системный прокси» — только браузеры и программы, которые "
                         "учитывают настройки прокси Windows; остальные работают как обычно.")
        self.mode = W.row(body, "Что пускать через VPN", lambda p: ctk.CTkSegmentedButton(
            p, values=list(vpn.MODES.values()), command=self._mode_changed))
        self.ru_direct = ctk.CTkSwitch(body, text="Раздельное туннелирование: российское — напрямую",
                                       command=self._ru_changed)
        self.ru_direct.pack(anchor="w", pady=(6, 3))
        ctk.CTkLabel(body, text="Через VPN идёт всё зарубежное. Напрямую, с домашнего IP, — сайты .ru, .рф, .su "
                                "со всеми поддоменами, российские IP-адреса и российские сервисы на «нерусских» "
                                "доменах (VK, Яндекс, банки и др.) по базам roscomvpn. YouTube, Telegram, GitHub "
                                "и Google Play всегда идут через VPN. Российский трафик не попадает на сервер "
                                "вовсе.", text_color=W.GRAY, anchor="w", justify="left",
                     wraplength=640, font=ctk.CTkFont(size=11)).pack(fill="x")
        fr = ctk.CTkFrame(body, fg_color="transparent")
        fr.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(fr, text="Порт системного прокси", width=230, anchor="w").pack(side="left")
        self.port = ctk.CTkEntry(fr, width=100)
        self.port.pack(side="left")
        ctk.CTkButton(fr, text="Применить", width=100, command=self._port_changed).pack(side="left", padx=8)

        body = W.section(self, "AmneziaWG",
                         "Перед включением отключитесь в приложении AmneziaVPN, если оно открыто, — два VPN "
                         "одновременно мешают друг другу. Раздельное туннелирование и режим «Системный прокси» "
                         "для AmneziaWG работают через sing-box — он скачается вместе с клиентом AmneziaWG.")
        self.lan_access = ctk.CTkSwitch(body, text="Без раздельного туннелирования: оставить доступ к локальной "
                                                   "сети", command=self._lan_changed)
        self.lan_access.pack(anchor="w", pady=3)
        ctk.CTkLabel(body, text="Принтеры, телефон для TG WS Proxy и т. п. Если выключить, интернет мимо VPN "
                                "блокируется полностью (kill switch). При раздельном туннелировании локальная "
                                "сеть доступна всегда.", text_color=W.GRAY, anchor="w",
                     justify="left", wraplength=640, font=ctk.CTkFont(size=11)).pack(fill="x")

        body = W.section(self, "Движки VPN", "Скачиваются с GitHub при первом включении и обновляются "
                                             "вместе с остальными компонентами.")
        self.engines = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=640)
        self.engines.pack(fill="x")
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))
        W.add_button(bar, "Лог VPN", self.open_log, secondary=True)
        W.add_button(bar, "Перезапустить VPN", self.restart, secondary=True, width=170)

        self.mode.set(vpn.MODES.get(s.get("vpn", "mode"), vpn.MODES["tun"]))
        W.set_switch(self.ru_direct, s.get("vpn", "ru_direct"))
        W.set_switch(self.lan_access, s.get("vpn", "lan_access"))
        W.set_entry(self.port, s.get("vpn", "proxy_port"))
        self.render()

    # ------------------------------------------------------------------ список серверов
    def on_show(self) -> None:
        self.render()

    def render(self) -> None:
        core = self.app.core
        for w in self.list_frame.winfo_children() + self.subs_frame.winfo_children():
            w.destroy()
        profiles = core.vpn_profiles()
        active = core.vpn_active()
        if not profiles:
            ctk.CTkLabel(self.list_frame, text="Серверов пока нет — добавьте ключ выше.", text_color=W.GRAY,
                         anchor="w").pack(fill="x", pady=4)
        for p in profiles:
            self._server_row(p, active is not None and p.id == active.id)
        subs = core.settings.get("vpn", "subscriptions") or []
        if subs:
            ctk.CTkLabel(self.subs_frame, text="Подписки", anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(fill="x", pady=(10, 2))
        for url in subs:
            fr = ctk.CTkFrame(self.subs_frame, fg_color="transparent")
            fr.pack(fill="x", pady=1)
            shown = url if len(url) <= 70 else url[:67] + "…"
            ctk.CTkLabel(fr, text=shown, anchor="w", text_color=W.GRAY).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(fr, text="✕", width=30, height=26, fg_color="transparent", text_color=W.P.muted,
                          hover_color=W.P.surface2, command=lambda u=url: self.remove_sub(u)).pack(side="right")
        self.subs_btn.configure(state="normal" if subs else "disabled")
        self.ping_btn.configure(state="normal" if profiles else "disabled")
        sb, aw = core.singbox, core.awg
        lines = [
            "VLESS — sing-box " + (sb.local_version() if sb.is_installed() else
                                   "(скачается с GitHub при первом включении)"),
            "AmneziaWG — официальный клиент AmneziaWG " + (aw.local_version() if aw.is_installed() else
                                                         "(скачается с GitHub при первом включении)"),
            "Раздельное туннелирование — базы roscomvpn " + (_rules_age(sb) or "(скачаются при включении)"),
        ]
        self.engines.configure(text="\n".join(lines))

    def _server_row(self, p: Profile, active: bool) -> None:
        P = W.P
        fr = ctk.CTkFrame(self.list_frame, fg_color=P.surface2, corner_radius=12,
                          border_width=2 if active else 0, border_color=P.accent if active else P.surface2)
        fr.pack(fill="x", pady=3)
        left = ctk.CTkFrame(fr, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=14, pady=8)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=("● " if active else "") + p.name, anchor="w",
                     text_color=P.accent if active else P.text,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(head, text=KIND_LABELS.get(p.kind, p.kind) + (" · из подписки" if p.sub else ""),
                     text_color=P.muted, font=ctk.CTkFont(size=11)).pack(side="left", padx=(10, 0))
        info = p.summary
        if p.id in self._pings:
            ms = self._pings[p.id]
            info += f" · {ms:.0f} мс" if ms is not None else " · не отвечает"
        ctk.CTkLabel(left, text=info, anchor="w", text_color=P.muted).pack(fill="x")
        ctk.CTkButton(fr, text="✕", width=34, height=30, fg_color="transparent", text_color=P.muted,
                      hover_color=P.border, command=lambda: self.remove(p)).pack(side="right", padx=(0, 10))
        ctk.CTkButton(fr, text="✎", width=34, height=30, fg_color="transparent", text_color=P.muted,
                      hover_color=P.border, command=lambda: self.rename(p)).pack(side="right")
        for w in (fr, left, head) + tuple(left.winfo_children()) + tuple(head.winfo_children()):
            if not isinstance(w, ctk.CTkButton):
                w.bind("<Button-1>", lambda _e, pid=p.id: self.select(pid), add="+")

    def select(self, profile_id: str) -> None:
        core = self.app.core
        if core.settings.get("vpn", "active") == profile_id:
            return
        core.vpn_select(profile_id)
        self.render()
        self.app.pages["home"][1].reload_vpn()
        self.app.pages["home"][1].apply_vpn("Переключение сервера…")

    def rename(self, p: Profile) -> None:
        dlg = ctk.CTkInputDialog(text="Новое название сервера:", title="Переименовать")
        name = dlg.get_input()
        if name and name.strip():
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
        if was_active is not None and was_active.id == p.id and self.app.vpn_state.running:
            if core.vpn_active() is not None:
                home.apply_vpn("Переключение сервера…")
            else:
                home.toggle_vpn(False)

    def remove_sub(self, url: str) -> None:
        if not self.app.ask("Удалить подписку и все её серверы?\n" + url):
            return
        self.app.core.vpn_remove_subscription(url)
        self.render()
        self.app.pages["home"][1].reload_vpn()

    # ------------------------------------------------------------------ добавление
    def add_from_box(self) -> None:
        self._add(self.input.get("1.0", "end-1c"), clear=True)

    def add_from_clipboard(self) -> None:
        try:
            text = self.app.clipboard_get()
        except Exception:
            text = ""
        if not text.strip():
            self.app.info("Буфер обмена пуст — скопируйте ключ и нажмите ещё раз.")
            return
        self._add(text)

    def add_from_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.app, title="Конфиг AmneziaWG",
                                          filetypes=[("Конфиг AmneziaWG / WireGuard", "*.conf"),
                                                     ("Все файлы", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read(1024 * 1024)
        except OSError as exc:
            self.app.error(str(exc), "Конфиг")
            return
        from pathlib import Path
        self._add(text, name=Path(path).stem)  # имя сервера — по имени файла, как в AmneziaWG

    def _add(self, text: str, clear: bool = False, name: str = "") -> None:
        if not text.strip():
            self.app.info("Вставьте ключ в поле выше.")
            return
        self.add_btn.configure(state="disabled")
        core = self.app.core

        def work():
            added, problems = core.vpn_add(text)
            if name and len(added) == 1 and added[0].kind == "awg":
                core.vpn_rename(added[0].id, name)
            return added, problems

        def done(result, err):
            self.add_btn.configure(state="normal")
            if err:
                self.app.error(str(err), "VPN")
                return
            added, problems = result
            if clear and added:
                W.set_text(self.input, "")
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
        def done(result, err):
            if err:
                self.app.error(str(err), "Подписки")
                return
            total, problems = result
            self.render()
            self.app.pages["home"][1].reload_vpn()
            text = f"Серверов в подписках: {total}"
            if problems:
                text += "\n\n" + "\n".join("• " + x for x in problems[:8])
            self.app.info(text, "Подписки")
        self.app.run_task(self.app.core.vpn_refresh_subscriptions, done)

    def ping_all(self) -> None:
        profiles = self.app.core.vpn_profiles()
        self.ping_btn.configure(state="disabled")

        def work():
            from concurrent.futures import ThreadPoolExecutor

            def one(p: Profile) -> Optional[float]:
                try:
                    return vpn.ping(vpn.parse_vless(p.data))
                except Exception:
                    return None
            # у AmneziaWG (UDP) сервер не отвечает посторонним — время не измерить
            vless = [p for p in profiles if p.kind == "vless"]
            with ThreadPoolExecutor(8) as ex:
                return dict(zip([p.id for p in vless], ex.map(one, vless)))

        def done(result, err):
            if not err:
                self._pings = result
            self.render()
        self.app.run_task(work, done)

    # ------------------------------------------------------------------ режимы
    def _mode_changed(self, label: str) -> None:
        mode = next((k for k, v in vpn.MODES.items() if v == label), "tun")
        self.app.core.settings.set("vpn", "mode", mode)
        self.app.pages["home"][1].apply_vpn("Смена режима…")

    def _ru_changed(self) -> None:
        self.app.core.settings.set("vpn", "ru_direct", bool(self.ru_direct.get()))
        self.app.pages["home"][1].apply_vpn("Применение настроек…")

    def _lan_changed(self) -> None:
        self.app.core.settings.set("vpn", "lan_access", bool(self.lan_access.get()))
        vs = self.app.vpn_state
        if vs.kind == "awg" and not (vs.singbox and vs.singbox.running):
            self.app.pages["home"][1].apply_vpn("Применение настроек…")

    def _port_changed(self) -> None:
        try:
            port = int(self.port.get().strip())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.app.error("Порт — число от 1024 до 65535", "VPN")
            return
        self.app.core.settings.set("vpn", "proxy_port", port)
        if self.app.vpn_state.mode == "proxy":
            self.app.pages["home"][1].apply_vpn("Применение настроек…")

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


def _rules_age(manager) -> str:
    import time
    files = [p for p in manager.rule_set_paths().values() if p.exists()]
    if not files:
        return ""
    newest = max(p.stat().st_mtime for p in files)
    return "от " + time.strftime("%d.%m.%Y", time.localtime(newest))


def profile_names(profiles: List[Profile]) -> List[str]:
    """Подписи для выпадающего списка на главной (имена могут повторяться)."""
    seen: Dict[str, int] = {}
    out = []
    for p in profiles:
        n = seen.get(p.name, 0) + 1
        seen[p.name] = n
        out.append(p.name if n == 1 else f"{p.name} ({n})")
    return out
