"""Элементы интерфейса Qt: карточки, ряды настроек, переключатель, плитки сервисов, градиентный фон."""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import palette as PL
from . import style as S

PAGE_MARGIN = 18


# ====================================================================== простые элементы

def label(text: str = "", role: str = "", wrap: bool = False, selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def set_label_color(lbl: QLabel, color: Optional[str]) -> None:
    """Цвет текста надписи (None — обычный из темы). Меняется, только если отличается."""
    css = f"color: {color};" if color else ""
    if lbl.styleSheet() != css:
        lbl.setStyleSheet(css)


def set_text(lbl, text: str) -> None:
    if lbl.text() != text:
        lbl.setText(text)


def button(text: str, on_click: Optional[Callable] = None, primary: bool = False, flat: bool = False,
           width: int = 0) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setObjectName("primary")
    elif flat:
        b.setObjectName("flat")
    if width:
        b.setMinimumWidth(width)
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def entry(placeholder: str = "", mono: bool = False) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    if mono:
        e.setProperty("role", "mono")
    return e


def textbox(height: int = 120, mono: bool = True, readonly: bool = False) -> QPlainTextEdit:
    t = QPlainTextEdit()
    if mono:
        t.setProperty("role", "mono")
    t.setMinimumHeight(height)
    t.setReadOnly(readonly)
    return t


def combo(values: Iterable[str] = (), on_change: Optional[Callable[[str], None]] = None) -> QComboBox:
    c = QComboBox()
    c.addItems(list(values))
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_change is not None:
        c.textActivated.connect(on_change)
    return c


def set_combo(c: QComboBox, values: List[str], current: str) -> None:
    """Пункты и текущее значение — без сигналов о выборе."""
    c.blockSignals(True)
    if [c.itemText(i) for i in range(c.count())] != values:
        c.clear()
        c.addItems(values)
    i = c.findText(current)
    c.setCurrentIndex(i if i >= 0 else (0 if values else -1))
    c.blockSignals(False)


def checkbox(text: str, on_toggle: Optional[Callable[[bool], None]] = None) -> QCheckBox:
    cb = QCheckBox(text)
    cb.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_toggle is not None:
        cb.toggled.connect(on_toggle)
    return cb


def set_checked(w, on: bool) -> None:
    """Состояние флажка или переключателя без сигнала (как будто пользователь не нажимал)."""
    if w.isChecked() != bool(on):
        w.blockSignals(True)
        w.setChecked(bool(on))
        w.blockSignals(False)
        w.update()


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_last: bool = True) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if isinstance(w, int):
            lay.addSpacing(w)
        elif w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


def button_bar(*buttons) -> QHBoxLayout:
    return hbox(*buttons, spacing=8, margins=(0, 8, 0, 0))


# ====================================================================== карточки

class Card(QFrame):
    """Полупрозрачная карточка со скруглёнными углами; содержимое — в self.body."""

    def __init__(self, parent: Optional[QWidget] = None, name: str = "card", margins=(18, 14, 18, 14),
                 spacing: int = 6):
        super().__init__(parent)
        self.setObjectName(name)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


# Какие сворачиваемые разделы раскрыты: окно подставляет сюда чтение и запись настроек
expanded_get: Callable[[str], bool] = lambda key: False
expanded_set: Callable[[str, bool], None] = lambda key, on: None


def help_badge(text: str) -> QLabel:
    """Кружок «?»: подробности — во всплывающей подсказке, а не абзацем на странице."""
    b = QLabel("?")
    b.setProperty("role", "help")
    b.setAlignment(Qt.AlignmentFlag.AlignCenter)
    b.setFixedSize(18, 18)
    b.setToolTip(f"<div style='max-width: 380px'>{text}</div>")
    b.setCursor(Qt.CursorShape.WhatsThisCursor)
    return b


def with_help(widget: QWidget, text: Optional[str]) -> QHBoxLayout:
    """Элемент и рядом «?» с подсказкой."""
    return hbox(widget, help_badge(text)) if text else hbox(widget)


class _SectionHeader(QAbstractButton):
    """Заголовок сворачиваемого раздела: стрелка и название, нажатие — свернуть или раскрыть."""

    def __init__(self, title: str):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.arrow = label("›", "h2")
        self.arrow.setFixedWidth(12)
        self.title = label(title, "h2")
        self.note = label("", "small")
        for w in (self.arrow, self.title, self.note):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        lay.addStretch(1)
        self.toggled.connect(lambda on: self.arrow.setText("⌄" if on else "›"))

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint()

    def paintEvent(self, _e) -> None:
        pass


class Section(Card):
    """Карточка раздела. hint — одна короткая строка под заголовком, help — подробности в «?»,
    collapsible — раздел сворачивается (для редких настроек); раскрытые запоминаются по key."""

    def __init__(self, title: str, hint: Optional[str] = None, help: Optional[str] = None,
                 collapsible: bool = False, key: str = ""):
        super().__init__()
        self.key = key or title
        self.header: Optional[_SectionHeader] = None
        top = QHBoxLayout()
        top.setSpacing(8)
        if collapsible:
            self.header = _SectionHeader(title)
            top.addWidget(self.header)
        else:
            top.addWidget(label(title, "h2"))
        if help:
            top.addWidget(help_badge(help))
        top.addStretch(1)
        self.body.addLayout(top)
        self.content = QWidget()
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        box = QVBoxLayout(self.content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        if hint:
            box.addWidget(label(hint, "muted", wrap=True))
        self.inner = QVBoxLayout()
        self.inner.setSpacing(8)
        self.inner.setContentsMargins(0, 4, 0, 0)
        box.addLayout(self.inner)
        self.body.addWidget(self.content)
        if self.header is not None:
            on = expanded_get(self.key)
            self.header.setChecked(on)
            self.content.setVisible(on)
            self.header.clicked.connect(self._toggle)

    def _toggle(self, on: bool) -> None:
        self.content.setVisible(on)
        expanded_set(self.key, on)

    def set_note(self, text: str) -> None:
        """Короткая сводка рядом с заголовком свёрнутого раздела («выключен», «2 программы»)."""
        if self.header is not None:
            set_text(self.header.note, text)


def section(layout: QVBoxLayout, title: str, hint: Optional[str] = None, help: Optional[str] = None,
            collapsible: bool = False, key: str = "") -> QVBoxLayout:
    """Карточка с заголовком; возвращает раскладку для содержимого (сама карточка — .card)."""
    card = Section(title, hint, help, collapsible, key)
    layout.addWidget(card)
    card.inner.card = card  # type: ignore[attr-defined]
    return card.inner


def subtitle(layout: QVBoxLayout, text: str) -> None:
    """Подзаголовок группы карточек на странице («Дополнительно»)."""
    lbl = label(text, "group")
    lbl.setContentsMargins(4, 6, 0, 0)
    layout.addWidget(lbl)


def row(layout: QVBoxLayout, text: str, widget: QWidget, hint: Optional[str] = None,
        label_width: int = 200, help: Optional[str] = None) -> QWidget:
    lbl = label(text)
    head = QWidget()
    head.setFixedWidth(label_width)
    hl = hbox(lbl, help_badge(help), spacing=6) if help else hbox(lbl)
    head.setLayout(hl)
    lay = hbox(head, widget, stretch_last=False)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy())
    layout.addLayout(lay)
    if hint:
        layout.addWidget(label(hint, "small", wrap=True))
    return widget


def page_title(layout: QVBoxLayout, text: str, subtitle: str = "") -> None:
    layout.addWidget(label(text, "title"))
    if subtitle:
        layout.addWidget(label(subtitle, "muted"))
    layout.addSpacing(2)


# ====================================================================== выбор из нескольких

class Segmented(QFrame):
    """Сегментированный выбор: ряд кнопок, нажата одна. changed(текст) — только по нажатию."""

    changed = Signal(str)

    def __init__(self, values: Iterable[str], on_change: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.setObjectName("segbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: Dict[str, QPushButton] = {}
        for v in values:
            b = QPushButton(v)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b)
            lay.addWidget(b, 1)
            self.buttons[v] = b
        self.group.buttonClicked.connect(lambda b: self.changed.emit(b.text()))
        if on_change is not None:
            self.changed.connect(on_change)

    def set(self, value: str) -> None:
        b = self.buttons.get(value)
        if b is None:
            self.group.setExclusive(False)
            for x in self.buttons.values():
                x.setChecked(False)
            self.group.setExclusive(True)
        elif not b.isChecked():
            b.setChecked(True)

    def get(self) -> str:
        b = self.group.checkedButton()
        return b.text() if b else ""


# ====================================================================== переключатель

class Toggle(QAbstractButton):
    """Переключатель как в iOS: дорожка цвета темы, белый ползунок с мягкой тенью, плавный ход."""

    def __init__(self, text: str = "", on_toggle: Optional[Callable[[bool], None]] = None, big: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(text)
        self._w, self._h = (52, 31) if big else (42, 25)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        if on_toggle is not None:
            self.clicked.connect(lambda checked: on_toggle(bool(checked)))

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, on: bool) -> None:
        if not self.isVisible() or self.signalsBlocked():
            self._pos = 1.0 if on else 0.0
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802 — имя Qt
        super().setChecked(on)
        if self.signalsBlocked():
            self._pos = 1.0 if on else 0.0
            self.update()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text()) + 10 if self.text() else 0
        return QSize(self._w + tw, max(self._h, fm.height()) + 4)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_switch(p, QRectF(1, (self.height() - self._h) / 2, self._w - 2, self._h), self._pos, self.isEnabled())
        if self.text():
            p.setPen(S.qcolor(S.P.text if self.isEnabled() else S.P.muted))
            r = self.rect().adjusted(self._w + 10, 0, 0, 0)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


def paint_switch(p: QPainter, track: QRectF, pos: float, enabled: bool = True,
                 on_color: Optional[QColor] = None) -> None:
    """Переключатель iOS в прямоугольнике track; pos — 0 (выкл) … 1 (вкл)."""
    h = track.height()
    path = QPainterPath()
    path.addRoundedRect(track, h / 2, h / 2)
    fill_c, fill_a = S.surface2_alpha()
    p.fillPath(path, S.qcolor(fill_c, min(1.0, fill_a * 1.6)))
    if pos > 0:
        on = QColor(on_color) if on_color is not None else QColor(S.P.accent)
        on.setAlphaF(pos * (1.0 if enabled else 0.45))
        p.fillPath(path, on)
    d = h - 4
    x = track.left() + 2 + (track.width() - 4 - d) * pos
    knob = QRectF(x, track.top() + 2, d, d)
    p.setPen(Qt.PenStyle.NoPen)
    for i, a in enumerate((0.10, 0.06, 0.03)):  # мягкая тень под ползунком
        p.setBrush(QColor(0, 0, 0, int(255 * a)))
        p.drawEllipse(knob.adjusted(-i * 0.6, 0.8 + i * 0.6, i * 0.6, 1.2 + i * 0.6))
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(knob)


# ====================================================================== плитка сервиса

TILE_ICONS = {"Zapret": "shield", "TG WS Proxy": "send", "VPN": "vpn"}


class PowerTile(QAbstractButton):
    """Большая плитка вкл/выкл на главной — как модуль Пункта управления Apple: значок в кружке,
    переключатель, название и состояние. Включённая подсвечена цветом темы."""

    def __init__(self, title: str, icon: str = ""):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title = title
        self.icon = icon or TILE_ICONS.get(title, "power")
        self.on = False
        self.busy = False
        self.status = ""
        self.details = ""
        self.setMinimumHeight(196)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = False

    def set(self, on: Optional[bool] = None, status: Optional[str] = None, details: Optional[str] = None,
            busy: Optional[bool] = None) -> None:
        new = (self.on if on is None else on, self.status if status is None else status,
               self.details if details is None else details, self.busy if busy is None else busy)
        if new != (self.on, self.status, self.details, self.busy):
            self.on, self.status, self.details, self.busy = new
            self.update()

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(220, 196)

    def _status_parts(self) -> tuple:
        """«● Работает» → (цвет точки, «Работает»)."""
        text = self.status or ""
        dot = None
        if text[:1] in ("●", "○", "⟳"):
            mark, text = text[0], text[1:].strip()
            dot = S.yellow() if (self.busy or mark == "⟳") else (S.green() if mark == "●" else S.P.muted)
        elif self.busy:
            dot = S.yellow()
        return dot, text

    def paintEvent(self, _e) -> None:
        from . import icons
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        dark = S.P.mode == "dark"
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        c, a = S.card_alpha()
        p.fillPath(path, S.qcolor(c, a * (1.25 if self._hover else 1.0)))
        accent = QColor(S.P.accent)
        if self.on:
            # мягкое свечение цвета темы из левого верхнего угла
            g = QRadialGradient(r.left() + 40, r.top() + 40, r.width() * 0.95)
            g.setColorAt(0, S.qcolor(S.P.accent, 0.34 if dark else 0.20))
            g.setColorAt(1, S.qcolor(S.P.accent, 0.0))
            p.fillPath(path, QBrush(g))
        p.setPen(QPen(S.qcolor(S.P.accent, 0.55) if self.on else
                      (S.qcolor("#ffffff", 0.09) if dark else S.qcolor("#000000", 0.07)), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        pad = 20
        # значок в кружке
        d = 46
        circle = QRectF(r.left() + pad, r.top() + pad, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        fill_c, fill_a = S.surface2_alpha()
        p.setBrush(accent if self.on else S.qcolor(fill_c, fill_a * 1.4))
        p.drawEllipse(circle)
        ic = PL.readable_on([S.P.accent]) if self.on else S.P.text
        icons.paint(p, self.icon, circle.adjusted(12, 12, -12, -12), ic, 1.9)
        # переключатель справа вверху
        tw, th = 48, 29
        paint_switch(p, QRectF(r.right() - pad - tw, r.top() + pad + (d - th) / 2, tw, th),
                     1.0 if self.on else 0.0, not self.busy)
        # название
        title_font = QFont(S.display_font_family())
        title_font.setPointSizeF(15)
        title_font.setWeight(QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(S.qcolor(S.P.text))
        top = circle.bottom() + 14
        p.drawText(QRectF(r.left() + pad, top, r.width() - 2 * pad, 26),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)
        # состояние с цветной точкой
        dot, text = self._status_parts()
        sf = QFont(self.font())
        sf.setPointSizeF(10)
        sf.setWeight(QFont.Weight.DemiBold)
        p.setFont(sf)
        x = r.left() + pad
        y = top + 28
        if dot:
            p.setBrush(QColor(dot))
            p.drawEllipse(QRectF(x, y + 7, 8, 8))
            x += 15
        p.setPen(S.qcolor(S.P.text if (self.on or self.busy) else S.P.muted))
        p.drawText(QRectF(x, y, r.right() - pad - x, 22), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   text)
        # подробности
        df = QFont(self.font())
        df.setPointSizeF(8.75)
        p.setFont(df)
        p.setPen(S.qcolor(S.P.muted))
        p.drawText(QRectF(r.left() + pad, y + 24, r.width() - 2 * pad, r.bottom() - y - 30),
                   Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.details)
        p.end()


def _ping_color(text: str) -> Optional[str]:
    """«48 мс» → цвет как у пинга серверов."""
    try:
        ms = float(text.split()[0])
    except (ValueError, IndexError):
        return None
    return S.green() if ms < 150 else (S.yellow() if ms < 350 else S.red())


def _font(size: float, weight=QFont.Weight.Normal, display: bool = False) -> QFont:
    f = QFont(S.display_font_family() if display else S.ui_font_family())
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def _card_path(p: QPainter, r: QRectF, radius: float, lit: bool, glow_at=None, glow: float = 0.34) -> QPainterPath:
    """Подложка карточки-«стекла»; lit — включённая: свечение цвета темы и кайма."""
    dark = S.P.mode == "dark"
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    c, a = S.card_alpha()
    p.fillPath(path, S.qcolor(c, a))
    if lit:
        x, y = glow_at or (r.left() + 40, r.top() + 40)
        g = QRadialGradient(x, y, r.width() * 0.8)
        g.setColorAt(0, S.qcolor(S.P.accent, glow if dark else glow * 0.6))
        g.setColorAt(1, S.qcolor(S.P.accent, 0.0))
        p.fillPath(path, QBrush(g))
    p.setPen(QPen(S.qcolor(S.P.accent, 0.5) if lit else S.hairline_qcolor(), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    return path


class HeroTile(PowerTile):
    """Главная карточка VPN: большая круглая кнопка включения, состояние, сервер с пингом,
    «чипы» режима и время подключения."""

    def __init__(self, title: str = "VPN"):
        super().__init__(title, "vpn")
        self.setMinimumHeight(196)
        self._since: Optional[float] = None
        from PySide6.QtCore import QTimer
        self._clock = QTimer(self)
        self._clock.timeout.connect(self.update)
        self._clock.start(1000)

    def sizeHint(self) -> QSize:
        return QSize(600, 196)

    def set(self, on=None, status=None, details=None, busy=None) -> None:
        import time
        was = self.on
        super().set(on, status, details, busy)
        if self.on and not was:
            self._since = time.monotonic()
        elif not self.on:
            self._since = None

    def _elapsed(self) -> str:
        import time
        if self._since is None or self.busy:
            return ""
        sec = int(time.monotonic() - self._since)
        return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

    def paintEvent(self, _e) -> None:
        from . import icons
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pad = 28
        rad = 62
        cx, cy = r.left() + pad + rad, r.center().y()
        _card_path(p, r, 24, self.on, (cx, cy), 0.42)
        # большая кнопка
        p.setPen(Qt.PenStyle.NoPen)
        if self.on and not self.busy:
            for k, a in ((22, 0.10), (12, 0.18)):
                p.setBrush(S.qcolor(S.P.accent, a))
                p.drawEllipse(QRectF(cx - rad - k, cy - rad - k, 2 * (rad + k), 2 * (rad + k)))
        fill_c, fill_a = S.surface2_alpha()
        circle = QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
        if self.on:
            g = QLinearGradient(circle.topLeft(), circle.bottomRight())
            acc = S.P.accents if len(S.P.accents) > 1 else [S.P.accent, PL.mix(S.P.accent, "#000000", 0.25)]
            for i, c in enumerate(acc):
                g.setColorAt(i / (len(acc) - 1), QColor(c))
            p.setBrush(QBrush(g))
        else:
            p.setBrush(S.qcolor(fill_c, fill_a * (2.0 if self._hover else 1.5)))
        p.drawEllipse(circle)
        if self.busy:
            p.setPen(QPen(QColor(S.yellow()), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(circle.adjusted(-7, -7, 7, 7), 90 * 16, -270 * 16)
        ic = PL.readable_on(S.P.accents) if self.on else S.P.text
        icons.paint(p, "power", circle.adjusted(rad * 0.58, rad * 0.58, -rad * 0.58, -rad * 0.58), ic, 2.0)
        # тексты
        x = circle.right() + 34
        width = r.right() - pad - x
        dot, text = self._status_parts()
        p.setFont(_font(9, QFont.Weight.DemiBold))
        p.setPen(S.qcolor(S.P.muted))
        p.drawText(QRectF(x, r.top() + 30, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "VPN")
        elapsed = self._elapsed()
        if elapsed:
            p.drawText(QRectF(x, r.top() + 30, width, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       "подключено " + elapsed)
        p.setFont(_font(22, QFont.Weight.Bold, display=True))
        p.setPen(QColor(dot) if (dot and self.on and not self.busy) else S.qcolor(S.P.text))
        p.drawText(QRectF(x, r.top() + 50, width, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   text or "—")
        lines = [ln for ln in (self.details or "").split("\n") if ln.strip()]
        y = r.top() + 98
        if lines:
            parts = [x_.strip() for x_ in lines[0].split(" · ")]
            ping = parts[-1] if len(parts) > 1 and parts[-1].endswith("мс") else ""
            name = " · ".join(parts[:-1] if ping else parts)
            icons.paint(p, "vpn", QRectF(x, y + 3, 18, 18), S.P.accent, 1.8)
            p.setFont(_font(12, QFont.Weight.DemiBold))
            p.setPen(S.qcolor(S.P.text))
            fm = p.fontMetrics()
            name = fm.elidedText(name, Qt.TextElideMode.ElideRight, int(width - 110))
            p.drawText(QRectF(x + 26, y, width - 26, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       name)
            if ping:
                px = x + 26 + fm.horizontalAdvance(name) + 12
                p.setFont(_font(10.5, QFont.Weight.Bold))
                p.setPen(QColor(_ping_color(ping) or S.P.muted))
                p.drawText(QRectF(px, y, 100, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, ping)
        # чипы режима
        chips = [c.strip() for ln in lines[1:] for c in ln.split(" · ") if c.strip()]
        cx_ = x
        y = r.top() + 138
        p.setFont(_font(8.75, QFont.Weight.Medium))
        fm = p.fontMetrics()
        for chip in chips:
            w = fm.horizontalAdvance(chip) + 22
            if cx_ + w > r.right() - pad:
                break
            pill = QPainterPath()
            pill.addRoundedRect(QRectF(cx_, y, w, 26), 13, 13)
            p.fillPath(pill, S.qcolor(S.P.accent, 0.18) if self.on else S.qcolor(fill_c, fill_a * 1.3))
            p.setPen(S.qcolor(S.P.text))
            p.drawText(QRectF(cx_, y, w, 26), Qt.AlignmentFlag.AlignCenter, chip)
            cx_ += w + 8
        if not chips and not self.on and not self.busy:
            p.setFont(_font(9))
            p.setPen(S.qcolor(S.P.muted))
            p.drawText(QRectF(x, y, width, 26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "Нажмите на кнопку, чтобы подключиться")
        p.end()


class ServiceTile(PowerTile):
    """Компактная плитка сервиса: значок, название, состояние, переключатель — в одну строку."""

    def __init__(self, title: str, icon: str = ""):
        super().__init__(title, icon)
        self.setMinimumHeight(96)

    def sizeHint(self) -> QSize:
        return QSize(360, 96)

    def paintEvent(self, _e) -> None:
        from . import icons
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pad = 18
        d = 46
        circle = QRectF(r.left() + pad, r.center().y() - d / 2, d, d)
        _card_path(p, r, 20, self.on, (circle.center().x(), circle.center().y()), 0.30)
        if self._hover and not self.busy:
            hp = QPainterPath()
            hp.addRoundedRect(r, 20, 20)
            p.fillPath(hp, S.qcolor(S.P.text, 0.03))
        fill_c, fill_a = S.surface2_alpha()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(S.P.accent) if self.on else S.qcolor(fill_c, fill_a * 1.4))
        p.drawEllipse(circle)
        icons.paint(p, self.icon, circle.adjusted(12, 12, -12, -12),
                    PL.readable_on([S.P.accent]) if self.on else S.P.text, 1.9)
        tw, th = 46, 28
        paint_switch(p, QRectF(r.right() - pad - tw, r.center().y() - th / 2, tw, th), 1.0 if self.on else 0.0,
                     not self.busy)
        x = circle.right() + 14
        width = r.right() - pad - tw - 12 - x
        p.setFont(_font(12.5, QFont.Weight.Bold, display=True))
        p.setPen(S.qcolor(S.P.text))
        top = r.center().y() - 31
        p.drawText(QRectF(x, top, width, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.title)
        dot, text = self._status_parts()
        p.setFont(_font(9.25, QFont.Weight.DemiBold))
        tx = x
        if dot:
            p.setBrush(QColor(dot))
            p.drawEllipse(QRectF(tx, top + 30, 7, 7))
            tx += 13
        p.setPen(S.qcolor(S.P.text if (self.on or self.busy) else S.P.muted))
        p.drawText(QRectF(tx, top + 24, width, 19), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   text)
        first = (self.details or "").split("\n")[0]
        p.setFont(_font(8.5))
        p.setPen(S.qcolor(S.P.muted))
        first = p.fontMetrics().elidedText(first, Qt.TextElideMode.ElideRight, int(width))
        p.drawText(QRectF(x, top + 44, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   first)
        p.end()


class StatusItem(QWidget):
    """Сервис в «Доступности»: значок в кружке, название, состояние цветом и задержка."""

    def __init__(self, name: str, icon: str):
        super().__init__()
        self.name, self.icon = name, icon
        self.state, self.color, self.extra = "—", None, ""
        self.setMinimumHeight(58)

    def set(self, state: str, color: Optional[str], extra: str = "") -> None:
        if (state, color, extra) != (self.state, self.color, self.extra):
            self.state, self.color, self.extra = state, color, extra
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(160, 58)

    def paintEvent(self, _e) -> None:
        from . import icons
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        d = 38
        circle = QRectF(0, (h - d) / 2, d, d)
        fill_c, fill_a = S.surface2_alpha()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(S.qcolor(self.color, 0.18) if self.color else S.qcolor(fill_c, fill_a * 1.4))
        p.drawEllipse(circle)
        icons.paint(p, self.icon, circle.adjusted(10, 10, -10, -10), self.color or S.P.text, 1.8)
        x = d + 12
        p.setFont(_font(10.5, QFont.Weight.DemiBold))
        p.setPen(S.qcolor(S.P.text))
        p.drawText(QRectF(x, h / 2 - 19, self.width() - x, 19), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   self.name)
        p.setFont(_font(8.75, QFont.Weight.Medium))
        p.setPen(QColor(self.color) if self.color else S.qcolor(S.P.muted))
        line = self.state + (f" · {self.extra}" if self.extra else "")
        p.drawText(QRectF(x, h / 2 + 1, self.width() - x, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   line)
        p.end()


# ====================================================================== градиентный фон

class GradientBackground(QWidget):
    """Фон окна. Страница — нейтральная база с мягким свечением цветов темы (как обои macOS),
    боковое меню — ровный цвет и тонкая граница справа."""

    def __init__(self, side: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.side = side
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self.side:
            p.fillRect(self.rect(), QColor(PL.gradient_at(S.P.side_stops, 0.5)))
            p.fillRect(QRectF(w - 1, 0, 1, h), QColor(S.hairline_qcolor()))
            p.end()
            return
        p.fillRect(self.rect(), QColor(S.P.bg))
        dark = S.P.mode == "dark"
        strength = (0.30 if dark else 0.20) * max(0.15, S.P.tint)
        acc = S.P.accents
        # свечение: первый цвет — справа сверху, последний — слева снизу, средний — по центру
        spots = [(acc[0], 0.92, -0.10, 0.85)]
        if len(acc) > 1:
            spots.append((acc[-1], 0.05, 1.08, 0.80))
        if len(acc) > 2:
            spots.append((acc[1], 0.55, 0.55, 0.55))
        for color, fx, fy, radius in spots:
            g = QRadialGradient(w * fx, h * fy, max(w, h) * radius)
            g.setColorAt(0, S.qcolor(color, strength))
            g.setColorAt(0.55, S.qcolor(color, strength * 0.35))
            g.setColorAt(1, S.qcolor(color, 0))
            p.fillRect(self.rect(), QBrush(g))
        p.end()


class Brand(QWidget):
    """Логотип в меню: значок-«скруглённый квадрат» с градиентом темы и название."""

    def __init__(self, name: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(10)
        self.badge = _BrandBadge()
        lay.addWidget(self.badge)
        lay.addWidget(label(name, "brand"))
        lay.addStretch(1)


class _BrandBadge(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 30)

    def paintEvent(self, _e) -> None:
        from . import icons
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 8.5, 8.5)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        acc = S.P.accents if len(S.P.accents) > 1 else [S.P.accent, PL.mix(S.P.accent, "#ffffff", 0.35)]
        for i, c in enumerate(acc):
            g.setColorAt(i / (len(acc) - 1), QColor(c))
        p.fillPath(path, QBrush(g))
        icons.paint(p, "bolt", r.adjusted(7, 7, -7, -7), PL.readable_on(S.P.accents), 2.0)
        p.end()


class GradientText(QLabel):
    """Надпись, залитая градиентом акцентных цветов (логотип)."""

    def __init__(self, text: str, point_size: float = 17):
        super().__init__(text)
        f = QFont(self.font())
        f.setPointSizeF(point_size)
        f.setBold(True)
        self.setFont(f)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        width = self.fontMetrics().horizontalAdvance(self.text())
        g = QLinearGradient(0, 0, max(1, width), 0)
        acc = S.P.accents if len(S.P.accents) > 1 else S.P.accents * 2
        for i, c in enumerate(acc):
            g.setColorAt(i / (len(acc) - 1), QColor(c))
        p.setPen(QPen(QBrush(g), 1))
        p.setFont(self.font())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())
        p.end()


# ====================================================================== значки

def icon_label(name: str, size: int = 16) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(size + 2, size + 2)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setProperty("icon_name", name)
    lbl.setProperty("icon_size", size)
    return lbl


def set_icon_color(lbl: QLabel, color: str) -> None:
    from . import icons
    lbl.setPixmap(icons.pixmap(lbl.property("icon_name"), color, int(lbl.property("icon_size")),
                               max(2.0, lbl.devicePixelRatioF())))


class NavButton(QPushButton):
    """Пункт бокового меню, как в macOS: значок цвета темы и название; выбранный — залит цветом темы."""

    def __init__(self, text: str, icon: str):
        super().__init__()
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(10)
        self.icon = icon_label(icon, 17)
        self.text_label = QLabel(text)
        for w in (self.icon, self.text_label):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.icon)
        lay.addWidget(self.text_label, 1)
        self.setMinimumHeight(36)
        self.toggled.connect(self._restyle)

    def _restyle(self, on: bool) -> None:
        on_accent = PL.readable_on([S.P.accent])
        color = on_accent if on else S.P.text
        css = f"color: {color}; font-weight: {600 if on else 500};"
        self.text_label.setStyleSheet(css)
        set_icon_color(self.icon, on_accent if on else S.P.accent)

    def restyle(self) -> None:
        self._restyle(self.isChecked())


def swatch_button(colors: List[str], size: int, selected: bool, on_click: Callable) -> "SwatchButton":
    """Кружок с градиентом набора цветов (выбранный — с белым кольцом)."""
    b = SwatchButton(colors, size, selected)
    b.clicked.connect(lambda _=False: on_click())
    return b


class SwatchButton(QAbstractButton):
    def __init__(self, colors: List[str], size: int, selected: bool = False):
        super().__init__()
        self.colors, self.selected = colors, selected
        self.setFixedSize(size + 6, size + 6)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 6
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        cols = self.colors if len(self.colors) > 1 else self.colors * 2
        for i, c in enumerate(cols):
            g.setColorAt(i / (len(cols) - 1), QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(r)
        if self.selected:
            p.setPen(QPen(QColor("#ffffff"), 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(3.5, 3.5, -3.5, -3.5))
        elif self.underMouse():
            p.setPen(QPen(S.qcolor(S.P.text, 0.5), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


def clear_layout(layout) -> None:
    """Убирает всё из раскладки. Виджеты сразу прячутся и отцепляются, иначе до
    deleteLater() они ещё рисуются поверх новых."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
            item.layout().deleteLater()


def grid(columns: int) -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(24)
    g.setVerticalSpacing(6)
    g.setContentsMargins(0, 0, 0, 0)
    for c in range(columns):
        g.setColumnStretch(c, 0)
    g.setColumnStretch(columns, 1)
    return g
