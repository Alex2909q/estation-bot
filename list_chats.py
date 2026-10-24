#!/usr/bin/env python3
"""Показує всі ваші групи й канали з ID — щоб легко заповнити GROUPS у .env.

Запуск:  python list_chats.py     (бота перед цим зупиніть)
"""
import asyncio
import getpass

from telethon import TelegramClient

import core
from bot import API_HASH, API_ID, SESSION_FILE


async def main() -> None:
    if not API_ID or not API_HASH:
        raise SystemExit("Спершу заповніть API_ID і API_HASH у .env")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start(
        phone=lambda: input("Номер телефону вашого акаунта (+380…): ").strip(),
        code_callback=lambda: input("Код, який прийшов у Telegram: ").strip(),
        password=lambda: getpass.getpass("Пароль двоетапної перевірки (якщо є): "),
    )
    print(f"\n{'ID для GROUPS':>16}  {'Тип':7} {'Юзернейм':28} Назва")
    print("-" * 90)
    async for d in client.iter_dialogs():
        if not (d.is_group or d.is_channel):
            continue
        kind = "група" if d.is_group else "канал"
        uname = core.primary_username(d.entity)
        print(f"{d.id:>16}  {kind:7} {('@' + uname) if uname else '(приватна)':28} {d.name}")
    print("\nУ GROUPS можна вписати або @юзернейм, або число з першої колонки.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
