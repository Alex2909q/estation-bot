#!/usr/bin/env python3
"""Створює секрет TG_SESSION для GitHub — потрібен для каналів, які без акаунта не читаються
(@dobrymao_UA, @dealradarua: у них вимкнена веб-версія).

Запуск на Mac: двічі клацніть make_session.command (або: python3 make_session.py).
Потрібні API_ID і API_HASH — у файлі .env поруч або у змінних середовища.

Результат — один рядок "api_id:api_hash:сесія", який копіюється в буфер обміну.
Цей рядок = доступ до вашого акаунта: вставляйте його лише в GitHub Secrets.
"""
import getpass
import os
import subprocess
from pathlib import Path


def read_env() -> dict:
    vals = {}
    path = Path(__file__).with_name(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                vals[key.strip()] = value.strip()
    return vals


def main() -> None:
    cfg = read_env()
    api_id = os.getenv("API_ID") or cfg.get("API_ID", "")
    api_hash = os.getenv("API_HASH") or cfg.get("API_HASH", "")
    if not api_id.isdigit() or not api_hash:
        raise SystemExit("Не знайдено API_ID / API_HASH у файлі .env")

    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    client = TelegramClient(StringSession(), int(api_id), api_hash, device_model="EStation GitHub")
    client.start(
        phone=lambda: input("Номер телефону вашого Telegram (+380…): ").strip(),
        code_callback=lambda: input("Код, який прийшов у Telegram: ").strip(),
        password=lambda: getpass.getpass("Пароль двоетапної перевірки (якщо є; символи не видно): "),
    )
    me = client.get_me()
    secret = f"{api_id}:{api_hash}:{client.session.save()}"
    client.disconnect()

    try:
        subprocess.run(["pbcopy"], input=secret.encode(), check=True)
        print(f"\n✅ Готово ({me.first_name}). Секрет TG_SESSION скопійовано в буфер обміну.")
        print("Вставте його (Cmd+V) у поле Secret на GitHub і натисніть Add secret.")
    except Exception:
        print("\n✅ Готово. Скопіюйте рядок нижче в секрет TG_SESSION на GitHub:\n")
        print(secret)


if __name__ == "__main__":
    main()
