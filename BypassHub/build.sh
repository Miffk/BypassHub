#!/bin/sh
# Сборка BypassHub для Linux одним файлом (нужен Python 3.10+): dist/BypassHub-linux-x86_64
set -e
cd "$(dirname "$0")"
python3 -m venv .venv-build
.venv-build/bin/pip install -r requirements.txt pyinstaller
.venv-build/bin/python -m PyInstaller --noconfirm --onefile --windowed \
  --name BypassHub-linux-x86_64 \
  --add-data "assets:assets" --collect-data customtkinter --collect-data certifi \
  main.py
echo
echo "Готово: dist/BypassHub-linux-x86_64"
