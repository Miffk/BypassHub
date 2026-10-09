"""Автоподбор стратегии zapret прямо в программе.

По очереди запускает каждую стратегию, проверяет сайты из utils/targets.txt
(HTTPS с TLS 1.2 и TLS 1.3 — как «Standard tests» в test zapret.ps1) и
выбирает стратегию с наибольшим числом успешных проверок; при равенстве —
с меньшей средней задержкой.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import netcheck
from .log import log
from .zapret import ZapretManager

DEFAULT_TARGETS = [
    "https://discord.com", "https://gateway.discord.gg", "https://cdn.discordapp.com",
    "https://updates.discord.com", "https://www.youtube.com", "https://youtu.be",
    "https://i.ytimg.com", "https://redirector.googlevideo.com", "https://www.google.com",
    "https://www.gstatic.com", "https://www.cloudflare.com", "https://cdnjs.cloudflare.com",
]
BASELINE = "(без обхода)"


def load_targets(path: Path) -> List[str]:
    """URL из targets.txt; строки PING: пропускаются — на ping обход не влияет."""
    urls = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r'\s*\w+\s*=\s*"(https?://[^"]+)"', line)
            if m:
                urls.append(m.group(1))
    except OSError:
        pass
    return urls or list(DEFAULT_TARGETS)


@dataclass
class StrategyResult:
    name: str
    ok: int = 0
    total: int = 0
    avg_ms: float = 0.0
    failed: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def score(self) -> tuple:
        return (self.ok, -self.avg_ms)


def pick_best(results: List[StrategyResult]) -> Optional[StrategyResult]:
    candidates = [r for r in results if r.name != BASELINE and not r.error and r.ok > 0]
    return max(candidates, key=lambda r: r.score) if candidates else None


def _measure(name: str, urls: List[str], timeout: float) -> StrategyResult:
    probes = netcheck.probe_many(urls, (netcheck.TLS12, netcheck.TLS13), timeout)
    good = [p for p in probes if p.ok]
    return StrategyResult(
        name, len(good), len(probes),
        sum(p.ms for p in good) / len(good) if good else 0.0,
        sorted({f"{p.url} ({p.tls}: {p.error})" for p in probes if not p.ok}),
    )


class StrategyTester:
    def __init__(self, zapret: ZapretManager, strategies: List[str], timeout: float = 5.0):
        self.zapret = zapret
        self.strategies = strategies
        self.timeout = timeout
        self.cancel = threading.Event()
        self.urls = load_targets(zapret.utils / "targets.txt")

    def run(self, on_progress: Callable[[int, int, str, Optional[StrategyResult]], None]) -> List[StrategyResult]:
        """on_progress(номер, всего, стратегия, результат предыдущего шага или None)."""
        results: List[StrategyResult] = []
        total = len(self.strategies) + 1
        on_progress(0, total, BASELINE, None)
        self.zapret.stop()
        time.sleep(1.0)
        res = _measure(BASELINE, self.urls, self.timeout)
        results.append(res)
        for i, name in enumerate(self.strategies, start=1):
            if self.cancel.is_set():
                break
            on_progress(i, total, name, res)
            try:
                self.zapret.start_process(name)
                time.sleep(1.0)  # дать WinDivert перехватить новые соединения
                res = _measure(name, self.urls, self.timeout)
            except Exception as exc:
                res = StrategyResult(name, error=str(exc).splitlines()[0])
            finally:
                self.zapret.kill_winws()
            results.append(res)
            log.info("Тест %s: %d/%d%s", name, res.ok, res.total, f" ({res.error})" if res.error else "")
        on_progress(total, total, "", res)
        return results
