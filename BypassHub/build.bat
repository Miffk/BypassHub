@echo off
chcp 65001 >nul
rem Сборка BypassHub.exe на Windows (нужен Python 3.10+)
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --uac-admin ^
  --name BypassHub --icon assets\icon.ico ^
  --add-data "assets;assets" --collect-data customtkinter ^
  --hidden-import pystray._win32 ^
  main.py || exit /b 1
echo.
echo Готово: dist\BypassHub.exe
