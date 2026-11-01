@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python не найден. Установите с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^) & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || (echo Ошибка установки зависимостей & pause & exit /b 1)
pyinstaller --noconfirm --clean --windowed --onefile --name TinySAStudio ^
  --hidden-import serial.tools.list_ports ^
  --hidden-import serial.tools.list_ports_windows ^
  tinysa_studio.py || (echo Ошибка сборки & pause & exit /b 1)
echo.
echo Готово: %~dp0dist\TinySAStudio.exe
pause
