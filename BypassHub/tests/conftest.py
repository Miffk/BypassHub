import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture
def windows_behavior(monkeypatch):
    """Проверить ветку кода для Windows и на Linux (функции смотрят winutil.IS_LINUX при вызове)."""
    from bypasshub import winutil
    monkeypatch.setattr(winutil, "IS_LINUX", False)
