"""Список серверов VPN с пингом и выбором — на главной (компактный) и во вкладке VPN."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Optional

import customtkinter as ctk

from .. import vpn
from ..vpn import Profile
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

KIND_LABELS = {"vless": "VLESS", "awg": "AmneziaWG"}


def ping_text(ms: Optional[float], measured: bool):
    """(текст, цвет) для задержки."""
    if not measured:
        return "—", W.GRAY
    if ms is None:
        return "нет ответа", W.RED
    if ms < 150:
        return f"{ms:.0f} мс", W.GREEN
    if ms < 350:
        return f"{ms:.0f} мс", W.YELLOW
    return f"{ms:.0f} мс", W.RED


class ServerPicker(ctk.CTkFrame):
    """Строки серверов: нажатие выбирает сервер; справа — пинг. Первая строка —
    «Авто — лучший сервер» (если серверов VLESS хотя бы два)."""

    def __init__(self, parent, app: "App", compact: bool = False,
                 on_rename: Optional[Callable[[Profile], None]] = None,
                 on_remove: Optional[Callable[[Profile], None]] = None):
        super().__init__(parent, fg_color="transparent", height=1)
        self.app = app
        self.compact = compact
        self.on_rename, self.on_remove = on_rename, on_remove
        self._sig = None
        self._rows: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    def render(self, force: bool = False) -> None:
        core = self.app.core
        profiles = core.vpn_profiles()
        auto_ok = len(core.vpn_vless()) >= 2
        active = core.vpn_active()
        selected = vpn.AUTO_TAG if core.vpn_is_auto() else (active.id if active else "")
        sig = (tuple((p.id, p.name, p.kind) for p in profiles), auto_ok, selected)
        if force or sig != self._sig:
            self._sig = sig
            self._build(profiles, auto_ok, selected)
        self._update()

    def _build(self, profiles, auto_ok: bool, selected: str) -> None:
        for w in self.winfo_children():
            w.destroy()
        self._rows = {}
        if not profiles:
            ctk.CTkLabel(self, text="Серверов пока нет — добавьте ключ во вкладке VPN.", text_color=W.GRAY,
                         anchor="w").pack(fill="x", pady=4)
            return
        if auto_ok:
            self._row(vpn.AUTO_TAG, "⚡ Авто — лучший сервер",
                      "Сам выбирает самый быстрый сервер VLESS и переключается, если он перестал отвечать",
                      selected == vpn.AUTO_TAG, None)
        for p in profiles:
            badge = ("Hysteria2" if p.data.lower().startswith(vpn.HYSTERIA2_SCHEMES) else
                     KIND_LABELS.get(p.kind, p.kind)) + (" · из подписки" if p.sub and not self.compact else "")
            self._row(p.id, p.name, p.summary if not self.compact else badge, p.id == selected, p,
                      badge=None if self.compact else badge)

    def _row(self, rid: str, title: str, subtitle: str, active: bool, profile: Optional[Profile],
             badge: Optional[str] = None) -> None:
        P = W.P
        fr = ctk.CTkFrame(self, fg_color=P.surface2, corner_radius=12, border_width=2 if active else 0,
                          border_color=P.accent if active else P.surface2)
        fr.pack(fill="x", pady=3)
        left = ctk.CTkFrame(fr, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=14, pady=7 if self.compact else 8)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x")
        name = ctk.CTkLabel(head, text=("● " if active else "") + title, anchor="w",
                            text_color=P.accent if active else P.text, font=ctk.CTkFont(size=14, weight="bold"))
        name.pack(side="left")
        if badge:
            ctk.CTkLabel(head, text=badge, text_color=P.muted, font=ctk.CTkFont(size=11)).pack(side="left",
                                                                                              padx=(10, 0))
        now = ctk.CTkLabel(head, text="", text_color=W.GREEN, font=ctk.CTkFont(size=11, weight="bold"))
        now.pack(side="left", padx=(10, 0))
        sub = ctk.CTkLabel(left, text=subtitle, anchor="w", justify="left", text_color=P.muted,
                           wraplength=520, font=ctk.CTkFont(size=12 if not self.compact else 11))
        sub.pack(fill="x")
        if profile is not None and not self.compact:
            if self.on_remove:
                ctk.CTkButton(fr, text="✕", width=34, height=30, fg_color="transparent", text_color=P.muted,
                              hover_color=P.border, command=lambda: self.on_remove(profile)
                              ).pack(side="right", padx=(0, 10))
            if self.on_rename:
                ctk.CTkButton(fr, text="✎", width=34, height=30, fg_color="transparent", text_color=P.muted,
                              hover_color=P.border, command=lambda: self.on_rename(profile)).pack(side="right")
        ping = ctk.CTkLabel(fr, text="", width=90, anchor="e", font=ctk.CTkFont(size=13, weight="bold"))
        ping.pack(side="right", padx=(6, 14 if self.compact or profile is None else 4))
        home = self.app.pages["home"][1]
        for w in (fr, left, head, name, sub, now, ping):
            w.bind("<Button-1>", lambda _e, r=rid: home.select_server(r), add="+")
            w.configure(cursor="hand2")
        self._rows[rid] = {"ping": ping, "now": now, "sub": sub, "profile": profile}

    def _update(self) -> None:
        """Пинг и «сейчас используется» — без пересоздания строк (обновляется каждые 3 с)."""
        home = self.app.pages["home"][1]
        vs = self.app.vpn_state
        live = vs.running and vs.kind == "vless"
        names = {p.id: p.name for p in self.app.core.vpn_profiles()}
        for rid, row in self._rows.items():
            profile = row["profile"]
            if rid == vpn.AUTO_TAG:
                cur = vs.current if live and vs.selected == vpn.AUTO_TAG else ""
                W.update(row["now"], text=f"сейчас: {names.get(cur, cur)}" if cur else "")
                ms = home.vpn_pings.get(cur) if cur else None
                text, color = ping_text(ms, bool(cur) and cur in home.vpn_pings)
                W.update(row["ping"], text=text if cur else "", text_color=color)
                continue
            W.update(row["now"], text="● сейчас" if live and vs.current == rid and vs.selected == vpn.AUTO_TAG
                     else "")
            if profile is not None and profile.kind == "awg":
                W.update(row["ping"], text="", text_color=W.GRAY)  # UDP-сервер посторонним не отвечает
                continue
            text, color = ping_text(home.vpn_pings.get(rid), rid in home.vpn_pings)
            W.update(row["ping"], text=text, text_color=color)
