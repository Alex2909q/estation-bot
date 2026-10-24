#!/usr/bin/env bash
# Mac: правою кнопкою → Відкрити. Створює секрет TG_SESSION і копіює його в буфер обміну.
cd "$(dirname "$0")"
if ! python3 -c 'import sys' >/dev/null 2>&1; then
  echo "Потрібен Python 3: погодьтеся встановити «інструменти розробника» або візьміть його з python.org,"
  echo "потім запустіть цей файл ще раз."
  read -n 1 -s -r -p "Натисніть будь-яку клавішу…"; exit 1
fi
if [ ! -x .venv-session/bin/python ]; then
  echo "Готую все потрібне (один раз, ~30 секунд)…"
  python3 -m venv .venv-session && .venv-session/bin/python -m pip install -q "telethon>=1.36"
fi
.venv-session/bin/python make_session.py
echo
read -n 1 -s -r -p "Натисніть будь-яку клавішу, щоб закрити вікно…"
