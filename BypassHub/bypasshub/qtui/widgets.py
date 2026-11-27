"""Элементы интерфейса Qt: карточки, ряды настроек, переключатель, плитки сервисов, градиентный фон."""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
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
    """Переключатель с плавным ходом ползунка; дорожка включённого — градиент темы."""

    def __init__(self, text: str = "", on_toggle: Optional[Callable[[bool], None]] = None, big: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(text)
        self._w, self._h = (56, 30) if big else (40, 22)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
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
        h = self._h
        y = (self.height() - h) / 2
        track = QRectF(1, y, self._w - 2, h)
        path = QPainterPath()
        path.addRoundedRect(track, h / 2, h / 2)
        off = S.qcolor(S.P.text, 0.22)
        if self._pos > 0:
            g = QLinearGradient(track.topLeft(), track.topRight())
            acc = S.P.accents if len(S.P.accents) > 1 else S.P.accents * 2
            for i, c in enumerate(acc):
                col = QColor(c)
                col.setAlphaF(self._pos if self.isEnabled() else self._pos * 0.5)
                g.setColorAt(i / (len(acc) - 1), col)
            p.fillPath(path, off)
            p.fillPath(path, QBrush(g))
        else:
            p.fillPath(path, off)
        d = h - 6
        x = track.left() + 3 + (track.width() - 6 - d) * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff" if S.P.mode == "dark" or self._pos > 0.5 else "#fbfbfd"))
        p.drawEllipse(QRectF(x, y + 3, d, d))
        if self.text():
            p.setPen(S.qcolor(S.P.text if self.isEnabled() else S.P.muted))
            r = self.rect().adjusted(self._w + 10, 0, 0, 0)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


# ====================================================================== плитка сервиса

class PowerTile(QAbstractButton):
    """Большая плитка вкл/выкл на главной: включённая залита градиентом темы."""

    def __init__(self, title: str):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title = title
        self.on = False
        self.busy = False
        self.status = ""
        self.details = ""
        self.setMinimumHeight(190)
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
        return QSize(220, 190)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        if self.on:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            acc = S.P.accents if len(S.P.accents) > 1 else S.P.accents * 2
            for i, c in enumerate(acc):
                g.setColorAt(i / (len(acc) - 1), QColor(c))
            p.fillPath(path, QBrush(g))
            fg = QColor(PL.readable_on(S.P.accents))
            sub = QColor(fg)
            sub.setAlphaF(0.85)
        else:
            c, a = S.card_alpha()
            p.fillPath(path, S.qcolor(c, a))
            fg, sub = QColor(S.P.text), QColor(S.P.muted)
        if self._hover and not self.busy:
            p.fillPath(path, QColor(255, 255, 255, 26 if self.on else 12))
        pad = 22
        # переключатель в правом верхнем углу
        tw, th = 50, 28
        tx, ty = r.right() - pad - tw, r.top() + pad
        track = QPainterPath()
        track.addRoundedRect(QRectF(tx, ty, tw, th), th / 2, th / 2)
        p.fillPath(track, QColor(255, 255, 255, 80) if self.on else S.qcolor(S.P.text, 0.22))
        knob = th - 8
        kx = tx + (tw - 4 - knob if self.on else 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(kx, ty + 4, knob, knob))
        # тексты
        f = QFont(self.font())
        f.setPointSizeF(16)
        f.setBold(True)
        while f.pointSizeF() > 11 and p.fontMetrics().horizontalAdvance(self.title) > 0 and \
                self._text_width(f, self.title) > tx - r.left() - pad - 10:
            f.setPointSizeF(f.pointSizeF() - 0.5)
        p.setFont(f)
        p.setPen(fg)
        p.drawText(QRectF(r.left() + pad, ty, tx - r.left() - pad, th),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)
        f2 = QFont(self.font())
        f2.setPointSizeF(11)
        f2.setBold(True)
        p.setFont(f2)
        p.setPen(fg if (self.on or self.busy) else sub)
        p.drawText(QRectF(r.left() + pad, r.top() + 82, r.width() - 2 * pad, 24),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.status)
        f3 = QFont(self.font())
        f3.setPointSizeF(9)
        p.setFont(f3)
        p.setPen(sub)
        p.drawText(QRectF(r.left() + pad, r.top() + 110, r.width() - 2 * pad, r.height() - 120),
                   Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.details)
        p.end()

    @staticmethod
    def _text_width(font: QFont, text: str) -> float:
        from PySide6.QtGui import QFontMetricsF
        return QFontMetricsF(font).horizontalAdvance(text)


# ====================================================================== градиентный фон

class GradientBackground(QWidget):
    """Фон окна — вертикальный градиент по цветам темы (страница или боковое меню)."""

    def __init__(self, side: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.side = side
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        stops = S.P.side_stops if self.side else S.P.page_stops
        g = QLinearGradient(0, 0, 0, self.height())
        if len(stops) == 1:
            stops = stops * 2
        # «туда и обратно», как в прежнем окне: край — первый цвет, середина — последний
        seq = list(stops) + list(reversed(stops))[1:]
        for i, c in enumerate(seq):
            g.setColorAt(i / (len(seq) - 1), QColor(c))
        p.fillRect(self.rect(), QBrush(g))
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

def icon_label(name: str, size: int = 12) -> QLabel:
    glyph = S.ICONS.get(name, "")
    lbl = QLabel(glyph if S.icon_font_family() else "")
    f = QFont(S.icon_font_family() or S.ui_font_family())
    f.setPointSizeF(size)
    lbl.setFont(f)
    return lbl


class NavButton(QPushButton):
    """Пункт бокового меню: значок и название."""

    def __init__(self, text: str, icon: str):
        super().__init__()
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(12)
        self.icon = icon_label(icon, 11)
        self.text_label = QLabel(text)
        for w in (self.icon, self.text_label):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.icon)
        lay.addWidget(self.text_label, 1)
        self.setMinimumHeight(42)
        self.toggled.connect(self._restyle)

    def _restyle(self, on: bool) -> None:
        color = PL.readable_on(S.P.accents) if on else S.P.muted
        css = f"color: {color};" + (" font-weight: 600;" if on else "")
        self.text_label.setStyleSheet(css)
        self.icon.setStyleSheet(f"color: {color};")

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
