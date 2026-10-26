"""Список серверов VPN с пингом и выбором — на главной (компактный) и во вкладке VPN."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .. import vpn
from ..vpn import Profile
from . import style as S
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

KIND_LABELS = {"vless": "VLESS", "awg": "AmneziaWG"}


def ping_text(ms: Optional[float], measured: bool):
    """(текст, цвет) для задержки."""
    if not measured:
        return "—", S.P.muted
    if ms is None:
        return "нет ответа", S.red()
    if ms < 150:
        return f"{ms:.0f} мс", S.green()
    if ms < 350:
        return f"{ms:.0f} мс", S.yellow()
    return f"{ms:.0f} мс", S.red()


class _Row(QFrame):
    """Строка сервера: нажатие выбирает сервер."""

    def __init__(self, on_click: Callable[[], None]):
        super().__init__()
        self.setObjectName("row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_click = on_click

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._on_click()
        super().mouseReleaseEvent(e)


class ServerPicker(QWidget):
    """Строки серверов: нажатие выбирает сервер; справа — пинг. Первая строка —
    «Авто — лучший сервер» (если серверов VLESS хотя бы два)."""

    def __init__(self, app: "App", compact: bool = False,
                 on_rename: Optional[Callable[[Profile], None]] = None,
                 on_remove: Optional[Callable[[Profile], None]] = None):
        super().__init__()
        self.app = app
        self.compact = compact
        self.on_rename, self.on_remove = on_rename, on_remove
        self._sig = None
        self._rows: Dict[str, dict] = {}
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)

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
        W.clear_layout(self.lay)
        self._rows = {}
        if not profiles:
            self.lay.addWidget(W.label("Серверов пока нет — добавьте ключ во вкладке VPN.", "muted"))
            return
        if auto_ok:
            self._row(vpn.AUTO_TAG, "⚡ Авто — лучший сервер",
                      "Сам выбирает самый быстрый сервер (VLESS или Hysteria2) и переключается, если он перестал отвечать",
                      selected == vpn.AUTO_TAG, None)
        for p in profiles:
            badge = ("Hysteria2" if p.data.lower().startswith(vpn.HYSTERIA2_SCHEMES) else
                     KIND_LABELS.get(p.kind, p.kind)) + (" · из подписки" if p.sub and not self.compact else "")
            self._row(p.id, p.name, p.summary if not self.compact else badge, p.id == selected, p,
                      badge=None if self.compact else badge)

    def _row(self, rid: str, title: str, subtitle: str, active: bool, profile: Optional[Profile],
             badge: Optional[str] = None) -> None:
        home = self.app.pages["home"][1] if "home" in self.app.pages else None
        fr = _Row(lambda r=rid: (home or self.app.pages["home"][1]).select_server(r))
        fr.setProperty("active", "true" if active else "false")
        lay = QHBoxLayout(fr)
        lay.setContentsMargins(14, 8, 10, 8)
        left = QVBoxLayout()
        left.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(10)
        name = W.label(("● " if active else "") + title, "h2")
        if active:
            W.set_label_color(name, S.P.accent)
        head.addWidget(name)
        if badge:
            head.addWidget(W.label(badge, "small"))
        now = W.label("", "small")
        W.set_label_color(now, S.green())
        head.addWidget(now)
        head.addStretch(1)
        left.addLayout(head)
        sub = W.label(subtitle, "small", wrap=True)
        left.addWidget(sub)
        lay.addLayout(left, 1)
        ping = W.label("")
        ping.setMinimumWidth(80)
        ping.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        f = ping.font()
        f.setBold(True)
        ping.setFont(f)
        lay.addWidget(ping)
        if profile is not None and not self.compact:
            if self.on_rename:
                lay.addWidget(W.button("✎", lambda p=profile: self.on_rename(p), flat=True))
            if self.on_remove:
                lay.addWidget(W.button("✕", lambda p=profile: self.on_remove(p), flat=True))
        for w in (name, now, sub, ping):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lay.addWidget(fr)
        self._rows[rid] = {"ping": ping, "now": now, "profile": profile}

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
                W.set_text(row["now"], f"сейчас: {names.get(cur, cur)}" if cur else "")
                text, color = ping_text(home.vpn_pings.get(cur) if cur else None, bool(cur) and cur in home.vpn_pings)
                W.set_text(row["ping"], text if cur else "")
                W.set_label_color(row["ping"], color)
                continue
            W.set_text(row["now"], "● сейчас" if live and vs.current == rid and vs.selected == vpn.AUTO_TAG else "")
            if profile is not None and profile.kind == "awg":
                W.set_text(row["ping"], "")  # UDP-сервер посторонним не отвечает
                continue
            text, color = ping_text(home.vpn_pings.get(rid), rid in home.vpn_pings)
            W.set_text(row["ping"], text)
            W.set_label_color(row["ping"], color)

    def on_palette(self) -> None:
        self.render(force=True)
