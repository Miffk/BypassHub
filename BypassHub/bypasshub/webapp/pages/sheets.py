"""Окна поверх страницы: подбор стратегии, диагностика, текст (команда запуска), подключение телефона.
У каждого — schema() (что нарисовать) и actions (что делают его кнопки)."""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from ... import tgproxy, winutil, zapret_diag
from ...log import log
from .. import ui


class Sheet:
    title = ""

    def __init__(self, app):
        self.app = app
        self.actions: Dict[str, Callable[[Any], Any]] = {}

    def schema(self) -> Dict[str, Any]:
        return {"title": self.title, "items": self.items()}

    def items(self) -> List[Dict[str, Any]]:
        return []

    def on_close(self) -> bool:
        """False — закрыть пока нельзя (идёт работа)."""
        return True


class TextSheet(Sheet):
    def __init__(self, app, title: str, text: str):
        super().__init__(app)
        self.title, self.text = title, text
        self.actions = {"sheet.copy": lambda v: (app.clipboard_set(self.text), ui.done("Скопировано"))[1]}

    def items(self):
        return [{"type": "log", "text": self.text, "height": 260},
                ui.buttons(ui.button("sheet.copy", "Скопировать", icon="copy"), ui.button("sheet.close", "Закрыть"))]


class StrategySheet(Sheet):
    """Прогоняет все стратегии и предлагает лучшую (как «Автоподбор стратегии» в окне Qt)."""
    title = "Подбор стратегии"

    def __init__(self, app):
        super().__init__(app)
        from ...strategy_test import BASELINE, StrategyTester
        core = app.core
        self._prev = (core.settings.get("zapret", "enabled"), core.strategy, core.zapret_mode,
                      app.zs.service_installed, app.zs.service_strategy)
        names = core.zapret.strategies()
        self.order = [BASELINE] + names
        self.rows: Dict[str, tuple] = {n: ("ожидание", "muted") for n in self.order}
        self.tester = StrategyTester(core.zapret, names)
        self.status, self.status_color = "Подготовка…", ""
        self.progress = 0.0
        self.best = None
        self.running = True
        self.finished = False
        self.actions = {"sheet.stop": self._stop, "sheet.apply": self._apply}
        app.busy["zapret"] = "Идёт подбор стратегии…"
        threading.Thread(target=self._work, daemon=True, name="strategy-test").start()

    def items(self):
        rows = []
        for name in self.order:
            text, color = self.rows[name]
            star = self.best is not None and self.best.name == name
            rows.append({"id": name, "title": name, "right": text + ("  ★ лучшая" if star else ""),
                         "rightColor": color, "active": star})
        return [
            ui.text("Каждая стратегия запускается по очереди, и проверяется доступность Discord, YouTube, Google и "
                    "Cloudflare (TLS 1.2 и 1.3). Это займёт пару минут; обход на это время будет прерываться.",
                    "muted"),
            ui.text(self.status, color=self.status_color),
            {"type": "progress", "value": self.progress},
            {"type": "list", "id": "results", "rows": rows, "scroll": 300},
            ui.buttons(ui.button("sheet.apply", f"Применить {self.best.name}" if self.best else "Применить лучшую",
                                 primary=True, disabled=self.best is None or self.running),
                       ui.button("sheet.stop" if self.running else "sheet.close",
                                 "Остановить" if self.running else "Закрыть")),
        ]

    def _work(self):
        def progress(i, total, name, prev):
            if prev is not None:
                self._result(prev)
            if name:
                self.status, self.status_color = f"[{i + 1}/{total}] Проверяю: {name}", ""
                self.rows[name] = ("проверка…", "yellow")
            self.progress = i / max(1, total)
            self.app.refresh()
        try:
            results = self.tester.run(progress)
        except Exception as exc:
            self.running = False
            self.status, self.status_color = f"Ошибка: {exc}", "red"
            return
        from ...strategy_test import BASELINE, pick_best
        self.running = False
        for r in results:
            self._result(r)
        self.progress = 1.0
        self.best = pick_best(results)
        if self.best is None:
            self.status, self.status_color = ("Ни одна стратегия не помогла. Попробуйте включить Game Filter / IPSet "
                                              "или запустите диагностику."), "red"
            return
        base = next((r for r in results if r.name == BASELINE), None)
        self.status = f"Лучшая: {self.best.name} — {self.best.ok} из {self.best.total}" + (
            f" (без обхода: {base.ok} из {base.total})" if base else "")
        self.status_color = "green"
        self.app.refresh()

    def _result(self, r):
        if r.error:
            self.rows[r.name] = (f"не запустилась: {r.error}", "red")
            return
        share = r.ok / r.total if r.total else 0
        color = "green" if share >= 0.9 else ("yellow" if share >= 0.5 else "red")
        self.rows[r.name] = (f"{r.ok} из {r.total} проверок" + (f" · {r.avg_ms:.0f} мс" if r.ok else ""), color)

    def _stop(self, _):
        self.tester.cancel.set()
        self.status, self.status_color = "Останавливаю после текущей стратегии…", ""

    def _apply(self, _):
        if not self.best:
            return {}
        core = self.app.core
        core.settings.set("zapret", "strategy", self.best.name)
        core.settings.set("zapret", "mode", "service" if self._prev[3] else core.zapret_mode)
        self._finish(core.zapret_enable)
        self.app.sheet = None
        return ui.done(f"Стратегия {self.best.name} применена")

    def on_close(self) -> bool:
        if self.running:
            self._stop(None)
            return False
        if not self.finished:
            was_enabled, strategy, mode, was_service, service_strategy = self._prev
            core = self.app.core

            def restore():
                if was_service:
                    core.zapret.start((service_strategy + ".bat") if service_strategy else strategy, "service")
                elif was_enabled:
                    core.zapret.start(strategy, mode)
            self._finish(restore)
        return True

    def _finish(self, fn):
        self.finished = True
        self.app.busy.pop("zapret", None)
        self.app.run("zapret", fn, lambda _: self.app.check_services(), "Применение…", "Zapret")


class DiagnosticsSheet(Sheet):
    title = "Диагностика zapret"
    COLORS = {zapret_diag.OK: "green", zapret_diag.WARN: "yellow", zapret_diag.ERROR: "red"}
    MARKS = {zapret_diag.OK: "✔", zapret_diag.WARN: "?", zapret_diag.ERROR: "✖"}

    def __init__(self, app, checks):
        super().__init__(app)
        self.checks = checks
        self.actions = {"sheet.conflicts": self._conflicts, "sheet.discord": self._discord,
                        "sheet.link": lambda v: (app.open_url(str(v)), {})[1]}

    def items(self):
        rows = [{"id": str(i), "title": c.text, "code": self.MARKS[c.level], "rightColor": self.COLORS[c.level],
                 "actions": [{"id": "sheet.link", "label": "Подробнее", "arg": c.link}] if c.link else []}
                for i, c in enumerate(self.checks)]
        return [{"type": "list", "id": "checks", "rows": rows, "scroll": 360, "wrap": True},
                ui.buttons(ui.button("sheet.conflicts", "Удалить конфликтующие службы", primary=True),
                           ui.button("sheet.discord", "Очистить кэш Discord",
                                     confirm="Discord будет закрыт, его кэш (Cache, Code Cache, GPUCache) удалён. "
                                             "Продолжить?"),
                           ui.button("sheet.close", "Закрыть"))]

    def _conflicts(self, _):
        self.app.run("diag", zapret_diag.remove_conflicting_services,
                     lambda r: "Удалено: " + (", ".join(r) if r else "конфликтующих служб нет"), "", "Диагностика")

    def _discord(self, _):
        self.app.run("diag", zapret_diag.clear_discord_cache,
                     lambda r: self.app.alert("\n".join(r), "Кэш Discord"), "", "Диагностика")


def _qr_data_url(text: str, size: int = 240) -> str:
    """QR-код картинкой для страницы (data:image/png)."""
    import base64
    import io

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
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class PhoneSheet(Sheet):
    """Телефон в той же сети: прокси слушает 0.0.0.0, на телефоне — ссылка или QR-код."""
    title = "Подключить телефон"

    def __init__(self, app):
        super().__init__(app)
        self.server: Optional[str] = None
        self.actions = {"sheet.enable": self._enable, "sheet.disable": self._disable,
                        "sheet.server": lambda v: setattr(self, "server", str(v).split()[0]),
                        "sheet.copy": lambda v: (app.clipboard_set(str(v)), ui.done("Ссылка скопирована"))[1]}

    def items(self):
        tg = self.app.core.tg
        out: List[Dict[str, Any]] = [ui.text("Телефон должен быть в той же Wi-Fi-сети, а компьютер — включён.", "muted")]
        if not tg.is_installed():
            return out + [ui.text("tg-ws-proxy ещё не загружен.", color="yellow")]
        if not tg.phone_access():
            out.append(ui.text("Сейчас прокси принимает подключения только с этого компьютера. Чтобы подключить "
                               "телефон, BypassHub откроет его для домашней сети (адрес 0.0.0.0) и добавит правило в "
                               + ("брандмауэр (ufw или firewalld, если он включён). " if winutil.IS_LINUX
                                  else "брандмауэр Windows. ") + "Выключить можно здесь же в любой момент."))
            out.append(ui.buttons(ui.button("sheet.enable", "Разрешить доступ с телефона", primary=True),
                                  ui.button("sheet.close", "Закрыть")))
            return out
        lan = tgproxy.lan_addresses()
        if not lan:
            return out + [ui.text("Не найден адрес компьютера в локальной сети. Подключите компьютер к роутеру "
                                  "(Wi-Fi или кабель).", color="red"), ui.buttons(ui.button("sheet.close", "Закрыть"))]
        ips = [ip for ip, _ in lan]
        server = self.server if self.server in ips else ips[0]
        link = tgproxy.phone_link(tg.load_config(), server)
        out.append({"type": "qr", "src": _qr_data_url(link), "steps": [
            "Откройте камеру телефона и наведите её на QR-код.", "Откройте ссылку — запустится Telegram.",
            "Нажмите «Подключить прокси»."]})
        if len(ips) > 1:
            out.append(ui.select("sheet.server", [f"{ip}  ({name})" for ip, name in lan],
                                 f"{server}  ({dict(lan)[server]})", "Адрес компьютера"))
        else:
            out.append(ui.row("Адрес компьютера", ui.text(server, "bold")))
        vpns = tgproxy.vpn_adapters()
        if vpns:
            out.append(ui.text("⚠ Включён VPN: " + ", ".join(vpns) + ". VPN может не пропускать подключения из "
                               "домашней сети — разрешите в его настройках доступ к локальной сети или временно "
                               "выключите его.", color="yellow"))
        else:
            out.append(ui.text("Если телефон не подключается, проверьте, что он в той же Wi-Fi-сети (не в гостевой) "
                               "и что брандмауэр не блокирует BypassHub.", "small"))
        out.append(ui.buttons(ui.button("sheet.copy", "Скопировать ссылку", primary=True, arg=link, icon="copy"),
                              ui.button("sheet.disable", "Отключить доступ"), ui.button("sheet.close", "Закрыть")))
        return out

    def _enable(self, _):
        self.app.run("tg", lambda: self.app.core.tg.set_phone_access(True), None, "Открываю доступ…",
                     "Доступ с телефона")

    def _disable(self, _):
        self.app.run("tg", lambda: self.app.core.tg.set_phone_access(False), None, "Закрываю доступ…",
                     "Доступ с телефона")
        self.app.sheet = None
