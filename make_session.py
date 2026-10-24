#!/usr/bin/env python3
"""Створює рядок сесії TG_SESSION для GitHub (потрібен лише для каналів без веб-версії).

Запуск на своєму комп'ютері:  python3 make_session.py
Скопіюйте виведений рядок у секрет TG_SESSION репозиторію. Цей рядок = доступ до
вашого акаунта: не показуйте його нікому й не комітьте у файли.
"""
import getpass

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

from bot import API_HASH, API_ID

if not API_ID or not API_HASH:
    raise SystemExit("Спершу впишіть API_ID і API_HASH у .env")

client = TelegramClient(StringSession(), API_ID, API_HASH)
client.start(
    phone=lambda: input("Номер телефону (+380…): ").strip(),
    code_callback=lambda: input("Код із Telegram: ").strip(),
    password=lambda: getpass.getpass("Пароль двоетапної перевірки (якщо є): "),
)
print("\nСкопіюйте рядок нижче в секрет TG_SESSION (Settings → Secrets and variables → Actions):\n")
print(client.session.save())
client.disconnect()
