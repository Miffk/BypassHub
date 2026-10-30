#!/bin/sh
# Сборка BypassHub для Linux одним файлом (нужен Python 3.10+): dist/BypassHub-linux-x86_64
set -e
cd "$(dirname "$0")"
# AmneziaWG для Linux — то же ядро, что в Android-версии (нужен Go)
(cd ../Android/core && CGO_ENABLED=0 go build \
  -tags with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api,badlinkname,tfogo_checklinkname0 \
  -trimpath -ldflags "-s -w -checklinkname=0" -o ../../BypassHub/bypasshub-awg ./cmd/bypasshub-awg)
python3 -m venv .venv-build
.venv-build/bin/pip install -r requirements.txt pyinstaller
.venv-build/bin/python -m PyInstaller --noconfirm --onefile --windowed \
  --name BypassHub-linux-x86_64 \
  --add-data "assets:assets" --add-binary "bypasshub-awg:bin" --collect-data customtkinter --collect-data certifi \
  --collect-submodules httpx --collect-submodules httpcore --collect-submodules anyio --collect-submodules h2 \
  main.py
echo
echo "Готово: dist/BypassHub-linux-x86_64"
