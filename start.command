#!/usr/bin/env bash
# Запуск на Mac: двічі клацніть цей файл (перший раз — правою кнопкою → «Відкрити»).
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
if ! grep -qE '^API_ID=[0-9]' .env; then
  echo "Впишіть API_ID і API_HASH у файл .env, збережіть і запустіть ще раз."
  open -e .env
  read -n 1 -s -r -p "Натисніть будь-яку клавішу, щоб закрити…"; exit 0
fi
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys' >/dev/null 2>&1; then
  echo "Потрібен Python 3. Встановіть його з https://www.python.org/downloads/ і запустіть ще раз."
  read -n 1 -s -r -p "Натисніть будь-яку клавішу…"; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Встановлюю залежності, це один раз…"
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
if launchctl print "gui/$(id -u)/ua.estation.monitor" >/dev/null 2>&1; then
  echo "Бот уже працює у фоні (автозапуск). Друга копія не потрібна."
  echo "Щоб вимкнути автозапуск — autostart-off.command"
  read -n 1 -s -r -p "Натисніть будь-яку клавішу…"; exit 0
fi
# caffeinate не дає Mac заснути від бездіяльності, поки бот працює
caffeinate -i .venv/bin/python bot.py
read -n 1 -s -r -p "Бот зупинився. Натисніть будь-яку клавішу…"
