from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Dict, List, Optional

import customtkinter as ctk

from ..strategy_test import BASELINE, StrategyResult, StrategyTester, pick_best
from . import widgets as W

if TYPE_CHECKING:
    from .app import App


class StrategyDialog(ctk.CTkToplevel):
    """Автоподбор: прогоняет все стратегии и предлагает лучшую."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        core = app.core
        self.title("Автоподбор стратегии")
        self.geometry("720x620")
        self.minsize(600, 480)
        self.configure(fg_color=W.P.bg)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._prev = (core.settings.get("zapret", "enabled"), core.strategy, core.zapret_mode,
                      app.zapret_status.service_installed, app.zapret_status.service_strategy)
        self.results: List[StrategyResult] = []
        self.rows: Dict[str, ctk.CTkLabel] = {}
        self.best: Optional[StrategyResult] = None
        self.running = True

        ctk.CTkLabel(self, text="Автоподбор стратегии", font=W.title_font(20), anchor="w").pack(
            fill="x", padx=20, pady=(18, 0))
        ctk.CTkLabel(self, text="Каждая стратегия запускается по очереди, и проверяется доступность Discord, "
                                "YouTube, Google и Cloudflare (TLS 1.2 и 1.3). Это займёт пару минут; обход "
                                "на это время будет прерываться.", text_color=W.P.muted, anchor="w",
                     justify="left", wraplength=660).pack(fill="x", padx=20)
        self.status = ctk.CTkLabel(self, text="Подготовка…", anchor="w")
        self.status.pack(fill="x", padx=20, pady=(12, 4))
        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20)

        self.list = ctk.CTkScrollableFrame(self, fg_color=W.P.surface, corner_radius=14)
        self.list.pack(fill="both", expand=True, padx=20, pady=12)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 16))
        self.apply_btn = W.GradientButton(bar, text="Применить лучшую", command=self._apply, width=220)
        self.apply_btn.configure(state="disabled")
        self.apply_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = W.add_button(bar, "Остановить", self._stop, secondary=True)

        strategies = core.zapret.strategies()
        self.tester = StrategyTester(core.zapret, strategies)
        for name in [BASELINE] + strategies:
            self._row(name, "ожидание", W.P.muted)

        home = app.pages["home"][1]
        home.zapret_card.set_busy(True, "Идёт подбор стратегии…")
        threading.Thread(target=self._work, daemon=True, name="strategy-test").start()
        self.after(100, self.lift)

    def _row(self, name: str, text: str, color) -> None:
        if name not in self.rows:
            fr = ctk.CTkFrame(self.list, fg_color="transparent")
            fr.pack(fill="x", pady=1)
            ctk.CTkLabel(fr, text=name, anchor="w", width=300).pack(side="left", padx=(8, 0))
            lbl = ctk.CTkLabel(fr, text="", anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            self.rows[name] = lbl
        self.rows[name].configure(text=text, text_color=color)

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
            self.status.configure(text=f"[{i + 1}/{total}] Проверяю: {name}")
            self._row(name, "проверка…", W.YELLOW)
        self.progress.set(i / total)

    def _show_result(self, r: StrategyResult) -> None:
        if r.error:
            self._row(r.name, f"не запустилась: {r.error}", W.RED)
            return
        share = r.ok / r.total if r.total else 0
        color = W.GREEN if share >= 0.9 else (W.YELLOW if share >= 0.5 else W.RED)
        text = f"{r.ok} из {r.total} проверок"
        if r.ok:
            text += f" · {r.avg_ms:.0f} мс"
        self._row(r.name, text, color)

    def _finish(self, results: List[StrategyResult], error) -> None:
        self.running = False
        self.results = results
        for r in results:
            self._show_result(r)
        self.progress.set(1)
        self.stop_btn.configure(text="Закрыть", command=self._close)
        if error:
            self.status.configure(text=f"Ошибка: {error}", text_color=W.RED)
            return
        self.best = pick_best(results)
        if self.best is None:
            self.status.configure(text="Ни одна стратегия не помогла. Попробуйте включить Game Filter / IPSet "
                                       "или запустите диагностику.", text_color=W.RED)
            return
        baseline = next((r for r in results if r.name == BASELINE), None)
        msg = f"Лучшая: {self.best.name} — {self.best.ok} из {self.best.total}"
        if baseline:
            msg += f" (без обхода: {baseline.ok} из {baseline.total})"
        self.status.configure(text=msg, text_color=W.GREEN)
        self._row(self.best.name, self.rows[self.best.name].cget("text") + "  ★ лучшая", W.GREEN)
        self.apply_btn.configure(text=f"Применить {self.best.name}", state="normal")

    def _stop(self) -> None:
        self.tester.cancel.set()
        self.status.configure(text="Останавливаю после текущей стратегии…")

    def _apply(self) -> None:
        if not self.best:
            return
        core = self.app.core
        core.settings.set("zapret", "strategy", self.best.name)
        mode = "service" if self._prev[3] else core.zapret_mode
        core.settings.set("zapret", "mode", mode)
        self._done(lambda: core.zapret_enable())

    def _close(self) -> None:
        if self.running:
            self._stop()
            return
        was_enabled, strategy, mode, was_service, service_strategy = self._prev
        core = self.app.core

        def restore():
            if was_service:
                core.zapret.start((service_strategy + ".bat") if service_strategy else strategy, "service")
            elif was_enabled:
                core.zapret.start(strategy, mode)
        self._done(restore)

    def _done(self, action) -> None:
        home = self.app.pages["home"][1]

        def finished(_, err):
            home.zapret_card.set_busy(False)
            home.reload_strategies()
            self.app.pages["zapret"][1].reload()
            home.check_services(delay_ms=2500)
            if err:
                self.app.error(str(err), "Zapret")
        self.app.run_task(action, finished)
        self.destroy()
