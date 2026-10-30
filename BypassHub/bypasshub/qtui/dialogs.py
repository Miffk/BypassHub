"""Окна: автоподбор стратегии, подключение телефона (QR-код), диагностика zapret, просмотр текста."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget)

from .. import tgproxy, winutil, zapret_diag
from ..strategy_test import BASELINE, StrategyResult, StrategyTester, pick_best
from . import style as S
from . import widgets as W

if TYPE_CHECKING:
    from .app import App


class _Dialog(QDialog):
    def __init__(self, app: "App", title: str, width: int = 720, height: int = 560):
        super().__init__(app)
        self.app = app
        self.setWindowTitle(title)
        self.resize(width, height)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 18, 22, 18)
        self.lay.setSpacing(8)
        from .app import set_dark_titlebar
        set_dark_titlebar(self, S.P.mode == "dark")


def _scroll_list() -> tuple:
    area = QScrollArea()
    area.setWidgetResizable(True)
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(4)
    area.setWidget(holder)
    card = W.Card(margins=(4, 4, 4, 4))
    card.body.addWidget(area)
    return card, lay


# ====================================================================== автоподбор стратегии

class StrategyDialog(_Dialog):
    """Прогоняет все стратегии и предлагает лучшую."""

    def __init__(self, app: "App"):
        super().__init__(app, "Автоподбор стратегии", 720, 620)
        core = app.core
        self._prev = (core.settings.get("zapret", "enabled"), core.strategy, core.zapret_mode,
                      app.zapret_status.service_installed, app.zapret_status.service_strategy)
        self.rows: Dict[str, QLabel] = {}
        self.best: Optional[StrategyResult] = None
        self.running = True
        self.lay.addWidget(W.label("Автоподбор стратегии", "h1"))
        self.lay.addWidget(W.label("Каждая стратегия запускается по очереди, и проверяется доступность Discord, "
                                   "YouTube, Google и Cloudflare (TLS 1.2 и 1.3). Это займёт пару минут; обход на это "
                                   "время будет прерываться.", "muted", wrap=True))
        self.status = W.label("Подготовка…")
        self.lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.lay.addWidget(self.progress)
        card, self.list = _scroll_list()
        self.lay.addWidget(card, 1)
        self.apply_btn = W.button("Применить лучшую", self._apply, primary=True, width=220)
        self.apply_btn.setEnabled(False)
        self.stop_btn = W.button("Остановить", self._stop)
        self.lay.addLayout(W.button_bar(self.apply_btn, self.stop_btn))

        strategies = core.zapret.strategies()
        self.tester = StrategyTester(core.zapret, strategies)
        for name in [BASELINE] + strategies:
            self._row(name, "ожидание", S.P.muted)
        self.list.addStretch(1)
        app.pages["home"][1].zapret_card.set_busy(True, "Идёт подбор стратегии…")
        threading.Thread(target=self._work, daemon=True, name="strategy-test").start()

    def _row(self, name: str, text: str, color: str) -> None:
        if name not in self.rows:
            lbl_name = W.label(name)
            lbl_name.setFixedWidth(300)
            lbl = W.label("")
            self.list.addLayout(W.hbox(lbl_name, lbl, stretch_last=False))
            self.rows[name] = lbl
        self.rows[name].setText(text)
        W.set_label_color(self.rows[name], color)

    def _work(self) -> None:
        def progress(i, total, name, prev):
            self.app.call_ui(lambda: self._on_progress(i, total, name, prev))
        try:
            results = self.tester.run(progress)
            self.app.call_ui(lambda: self._finish(results, None))
        except Exception as exc:
            self.app.call_ui(lambda e=exc: self._finish([], e))

    def _on_progress(self, i: int, total: int, name: str, prev: Optional[StrategyResult]) -> None:
        if prev is not None:
            self._show_result(prev)
        if name:
            self.status.setText(f"[{i + 1}/{total}] Проверяю: {name}")
            self._row(name, "проверка…", S.yellow())
        self.progress.setValue(int(i / max(1, total) * 1000))

    def _show_result(self, r: StrategyResult) -> None:
        if r.error:
            self._row(r.name, f"не запустилась: {r.error}", S.red())
            return
        share = r.ok / r.total if r.total else 0
        color = S.green() if share >= 0.9 else (S.yellow() if share >= 0.5 else S.red())
        self._row(r.name, f"{r.ok} из {r.total} проверок" + (f" · {r.avg_ms:.0f} мс" if r.ok else ""), color)

    def _finish(self, results: List[StrategyResult], error) -> None:
        self.running = False
        for r in results:
            self._show_result(r)
        self.progress.setValue(1000)
        self.stop_btn.setText("Закрыть")
        if error:
            self.status.setText(f"Ошибка: {error}")
            W.set_label_color(self.status, S.red())
            return
        self.best = pick_best(results)
        if self.best is None:
            self.status.setText("Ни одна стратегия не помогла. Попробуйте включить Game Filter / IPSet или "
                                "запустите диагностику.")
            W.set_label_color(self.status, S.red())
            return
        baseline = next((r for r in results if r.name == BASELINE), None)
        msg = f"Лучшая: {self.best.name} — {self.best.ok} из {self.best.total}"
        if baseline:
            msg += f" (без обхода: {baseline.ok} из {baseline.total})"
        self.status.setText(msg)
        W.set_label_color(self.status, S.green())
        self._row(self.best.name, self.rows[self.best.name].text() + "  ★ лучшая", S.green())
        self.apply_btn.setText(f"Применить {self.best.name}")
        self.apply_btn.setEnabled(True)

    def _stop(self) -> None:
        if not self.running:
            self.close()
            return
        self.tester.cancel.set()
        self.status.setText("Останавливаю после текущей стратегии…")

    def _apply(self) -> None:
        if not self.best:
            return
        core = self.app.core
        core.settings.set("zapret", "strategy", self.best.name)
        core.settings.set("zapret", "mode", "service" if self._prev[3] else core.zapret_mode)
        self._done(lambda: core.zapret_enable())

    def closeEvent(self, event) -> None:  # noqa: N802 — имя Qt
        if self.running:
            self._stop()
            event.ignore()
            return
        if not getattr(self, "_finished", False):
            was_enabled, strategy, mode, was_service, service_strategy = self._prev
            core = self.app.core

            def restore():
                if was_service:
                    core.zapret.start((service_strategy + ".bat") if service_strategy else strategy, "service")
                elif was_enabled:
                    core.zapret.start(strategy, mode)
            self._done(restore, close=False)
        event.accept()

    def _done(self, action, close: bool = True) -> None:
        self._finished = True
        home = self.app.pages["home"][1]

        def finished(_, err):
            home.zapret_card.set_busy(False)
            self.app.pages["zapret"][1].reload()
            home.check_services(delay_ms=2500)
            if err:
                self.app.error(str(err), "Zapret")
        self.app.run_task(action, finished)
        if close:
            self.close()


# ====================================================================== телефон

def qr_pixmap(text: str, size: int) -> QPixmap:
    """Чёткий QR-код: каждый модуль — целое число пикселей, на белой подложке."""
    import qrcode
    from PIL import Image, ImageDraw
    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    pad = max(8, size // 14)
    module = max(1, (size - 2 * pad) // n)
    full = module * n + 2 * pad
    img = Image.new("RGB", (full, full), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                x0, y0 = pad + x * module, pad + y * module
                d.rectangle([x0, y0, x0 + module - 1, y0 + module - 1], fill=(17, 19, 24))
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, full, full, full * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class PhoneDialog(_Dialog):
    """Открыть прокси для домашней сети и показать QR-код для Telegram на телефоне."""

    def __init__(self, app: "App"):
        super().__init__(app, "Подключить телефон", 560, 440)
        self.lay.addWidget(W.label("Telegram на телефоне через этот компьютер", "h2"))
        self.lay.addWidget(W.label("Телефон должен быть в той же Wi-Fi-сети, а компьютер — включён.", "muted"))
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.lay.addLayout(self.body)
        self.lay.addStretch(1)
        self.render()

    def _clear(self) -> None:
        W.clear_layout(self.body)

    def render(self, server: Optional[str] = None) -> None:
        self._clear()
        tg = self.app.core.tg
        if not tg.is_installed():
            lbl = W.label("tg-ws-proxy ещё не загружен.")
            W.set_label_color(lbl, S.yellow())
            self.body.addWidget(lbl)
            return
        if not tg.phone_access():
            self.body.addWidget(W.label(
                "Сейчас прокси принимает подключения только с этого компьютера. Чтобы подключить телефон, "
                "BypassHub откроет его для домашней сети (адрес 0.0.0.0) и добавит правило в брандмауэр "
                + ("(ufw или firewalld, если он включён). " if winutil.IS_LINUX else "Windows. ") +
                "Выключить можно здесь же в любой момент.", wrap=True))
            self.body.addLayout(W.button_bar(W.button("Разрешить доступ с телефона", self._enable, primary=True),
                                             W.button("Закрыть", self.close)))
            return
        lan = tgproxy.lan_addresses()
        if not lan:
            lbl = W.label("Не найден адрес компьютера в локальной сети. Подключите компьютер к роутеру (Wi-Fi или "
                          "кабель).", wrap=True)
            W.set_label_color(lbl, S.red())
            self.body.addWidget(lbl)
            return
        ips = [ip for ip, _ in lan]
        server = server if server in ips else ips[0]
        link = tgproxy.phone_link(tg.load_config(), server)
        row = QHBoxLayout()
        qr = QLabel()
        qr.setPixmap(qr_pixmap(link, 230))
        row.addWidget(qr)
        steps = QVBoxLayout()
        for i, text in enumerate(("Откройте камеру телефона и наведите её на QR-код.",
                                  "Откройте ссылку — запустится Telegram.", "Нажмите «Подключить прокси».")):
            num = W.label(str(i + 1))
            num.setFixedSize(24, 24)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(f"background: {S.P.accent}; color: {S.P.on_accent}; border-radius: 12px; "
                              "font-weight: 700;")
            steps.addLayout(W.hbox(num, W.label(text, wrap=True), stretch_last=False))
        steps.addStretch(1)
        row.addSpacing(12)
        row.addLayout(steps, 1)
        self.body.addLayout(row)
        if len(ips) > 1:
            labels = [f"{ip}  ({name})" for ip, name in lan]
            box = W.combo(labels, lambda v: self.render(v.split()[0]))
            W.set_combo(box, labels, labels[ips.index(server)])
            self.body.addLayout(W.hbox(W.label("Адрес компьютера:"), box))
        else:
            addr = W.label(server)
            f = addr.font()
            f.setBold(True)
            addr.setFont(f)
            self.body.addLayout(W.hbox(W.label("Адрес компьютера:"), addr))
        vpns = tgproxy.vpn_adapters()
        if vpns:
            warn = W.Card(name="warn", margins=(12, 8, 12, 8), spacing=2)
            title = W.label("⚠  Включён VPN: " + ", ".join(vpns))
            f = title.font()
            f.setBold(True)
            title.setFont(f)
            warn.body.addWidget(title)
            warn.body.addWidget(W.label(
                "VPN может не пропускать подключения из домашней сети — тогда телефон не подключится. Если так "
                "случилось: разрешите в настройках VPN доступ к локальной сети (обычно «Локальная сеть», «Allow LAN» "
                "или «Split tunneling»), либо временно выключите VPN. Адрес в QR-коде — от Wi-Fi/Ethernet, а не от "
                "VPN; если адресов несколько, выберите адрес вида 192.168.x.x.", wrap=True))
            self.body.addWidget(warn)
        else:
            self.body.addWidget(W.label("Если телефон не подключается, проверьте, что он в той же Wi-Fi-сети (не в "
                                        "гостевой) и что брандмауэр не блокирует BypassHub.", "small", wrap=True))
        self.body.addLayout(W.button_bar(W.button("Скопировать ссылку", lambda: self._copy(link), primary=True),
                                         W.button("Отключить доступ", self._disable),
                                         W.button("Закрыть", self.close)))

    def _copy(self, link: str) -> None:
        self.app.copy_to_clipboard(link)
        self.app.info("Ссылка скопирована. Отправьте её себе в Telegram и откройте на телефоне.")

    def _enable(self) -> None:
        def done(_, err):
            if err:
                self.app.error(f"Не удалось открыть доступ: {err}")
            if self.isVisible():
                self.render()
            self.app.pages["tg"][1].reload()
        self.app.run_task(lambda: self.app.core.tg.set_phone_access(True), done, "Доступ с телефона")

    def _disable(self) -> None:
        def done(_, err):
            if err:
                self.app.error(str(err))
            self.app.pages["tg"][1].reload()
            self.close()
        self.app.run_task(lambda: self.app.core.tg.set_phone_access(False), done, "Доступ с телефона")


# ====================================================================== диагностика и текст

class DiagnosticsDialog(_Dialog):
    def __init__(self, app: "App", checks):
        super().__init__(app, "Диагностика zapret", 780, 600)
        colors = {zapret_diag.OK: S.green(), zapret_diag.WARN: S.yellow(), zapret_diag.ERROR: S.red()}
        icons = {zapret_diag.OK: "✔", zapret_diag.WARN: "?", zapret_diag.ERROR: "✖"}
        card, lst = _scroll_list()
        for c in checks:
            icon = W.label(icons[c.level])
            icon.setFixedWidth(22)
            W.set_label_color(icon, colors[c.level])
            text = W.label(c.text, wrap=True, selectable=True)
            if c.level != zapret_diag.OK:
                W.set_label_color(text, colors[c.level])
            items = [icon, text]
            if c.link:
                items.append(W.button("Подробнее", lambda u=c.link: winutil.open_url(u)))
            lay = W.hbox(*items, stretch_last=False)
            lay.setStretch(1, 1)
            lst.addLayout(lay)
        lst.addStretch(1)
        self.lay.addWidget(card, 1)
        self.lay.addLayout(W.button_bar(W.button("Удалить конфликтующие службы", self._remove_conflicts, primary=True),
                                        W.button("Очистить кэш Discord", self._clear_discord, primary=True),
                                        W.button("Закрыть", self.close)))

    def _remove_conflicts(self) -> None:
        self.app.run_task(zapret_diag.remove_conflicting_services,
                          lambda r, e: self.app.error(str(e)) if e else
                          self.app.info("Удалено: " + (", ".join(r) if r else "конфликтующих служб нет")))

    def _clear_discord(self) -> None:
        if not self.app.ask("Discord будет закрыт, его кэш (Cache, Code Cache, GPUCache) удалён. Продолжить?"):
            return
        self.app.run_task(zapret_diag.clear_discord_cache,
                          lambda r, e: self.app.error(str(e)) if e else self.app.info("\n".join(r)))


class TextDialog(_Dialog):
    def __init__(self, app: "App", title: str, text: str):
        super().__init__(app, title, 760, 420)
        box = W.textbox(200, readonly=True)
        box.setPlainText(text)
        self.lay.addWidget(box, 1)
        self.lay.addLayout(W.button_bar(W.button("Скопировать", lambda: app.copy_to_clipboard(text)),
                                        W.button("Закрыть", self.close)))
