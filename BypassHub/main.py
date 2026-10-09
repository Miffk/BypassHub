"""BypassHub — точка входа."""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from bypasshub import APP_NAME, __version__, winutil
from bypasshub.log import log, setup_logging
from bypasshub.paths import Paths


def _wait_for_exit(pid: int, timeout: float = 30.0) -> None:
    import time
    try:
        import psutil
    except ImportError:
        time.sleep(5)
        return
    deadline = time.time() + timeout
    while time.time() < deadline and psutil.pid_exists(pid):
        time.sleep(0.3)


def main() -> int:
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--minimized", action="store_true", help="запуск свёрнутым в трей")
    ap.add_argument("--data-dir", type=Path, default=None, help="папка данных (по умолчанию C:\\ProgramData\\BypassHub)")
    ap.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)  # после самообновления
    args = ap.parse_args()

    if args.wait_pid:
        _wait_for_exit(args.wait_pid)

    if winutil.IS_WINDOWS and not winutil.is_admin():
        # zapret (WinDivert), службы и hosts требуют прав администратора
        if not winutil.relaunch_as_admin():
            winutil.message_box("Для работы zapret нужны права администратора.", APP_NAME, error=True)
        return 0

    paths = Paths(args.data_dir)
    paths.ensure()

    if not winutil.acquire_single_instance():
        paths.show_flag.touch()  # попросить уже запущенную копию показать окно
        return 0

    setup_logging(paths.log_file)
    # у exe без консоли sys.stderr/stdout = None: любая запись туда (например,
    # предупреждение библиотеки) иначе ломает обработчик событий окна
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(paths.logs / "console.log", "a", encoding="utf-8", buffering=1))
    log.info("%s %s запущен, данные: %s", APP_NAME, __version__, paths.root)

    from bypasshub.core import Core
    from bypasshub.gui.app import App

    core = Core(paths)
    minimized = args.minimized or bool(core.settings.get("app", "start_minimized"))
    app = App(core, start_minimized=minimized)
    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        text = traceback.format_exc()
        try:
            log.critical(text)
        except Exception:
            pass
        winutil.message_box(f"Непредвиденная ошибка:\n\n{text}", APP_NAME, error=True)
        sys.exit(1)
