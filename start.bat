@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul
findstr /r /c:"^API_ID=[0-9]" ".env" >nul
if errorlevel 1 (
    echo Впишіть API_ID і API_HASH у файл .env, збережіть його і запустіть start.bat ще раз.
    notepad ".env"
    pause
    exit /b
)
if not exist ".venv\Scripts\python.exe" (
    echo Встановлюю залежності, це один раз...
    py -3 -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" bot.py
pause
