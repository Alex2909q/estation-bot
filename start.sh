#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
if ! grep -qE '^API_ID=[0-9]' .env; then
  echo "Впишіть API_ID і API_HASH у файл .env (nano .env) і запустіть ./start.sh ще раз."
  exit 0
fi
if [ ! -x .venv/bin/python ]; then
  echo "Встановлюю залежності, це один раз…"
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python bot.py
