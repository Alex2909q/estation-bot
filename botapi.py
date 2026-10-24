"""Мінімальний клієнт Telegram Bot API (лише стандартна бібліотека Python)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import urllib.error
import urllib.request

log = logging.getLogger("monitor")


class BotAPI:
    """Мінімальний клієнт Telegram Bot API (лише стандартна бібліотека)."""

    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.ssl = ssl.create_default_context()
        for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.exists(bundle):           # системні сертифікати (macOS / Linux)
                try:
                    self.ssl.load_verify_locations(bundle)
                except Exception:
                    pass

    def _call_sync(self, method: str, params: dict, http_timeout: float) -> dict:
        req = urllib.request.Request(
            self.base + method,
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=http_timeout, context=self.ssl) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except ValueError:
                return {"ok": False, "error_code": e.code,
                        "description": body.decode("utf-8", "replace")[:300]}

    async def call(self, method: str, params: dict | None = None, http_timeout: float = 30) -> dict:
        return await asyncio.to_thread(self._call_sync, method, params or {}, http_timeout)

    async def send(self, chat_id, text: str, button_url: str | None = None) -> tuple[bool, dict]:
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": True}}
        if button_url:
            params["reply_markup"] = {"inline_keyboard": [[{"text": "Відкрити оголошення", "url": button_url}]]}
        res: dict = {}
        for attempt in range(4):
            try:
                res = await self.call("sendMessage", params)
            except Exception as e:  # мережа
                log.warning("Бот: не вдалося надіслати (%s), повтор…", e)
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if res.get("ok"):
                return True, res
            code, desc = res.get("error_code"), str(res.get("description", ""))
            if code == 429:
                await asyncio.sleep(int(res.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            if code == 400 and "reply_markup" in params and ("button" in desc.lower() or "url" in desc.lower()):
                params.pop("reply_markup")      # кнопку з таким посиланням Telegram не прийняв
                continue
            if code in (400, 401, 403, 404):
                return False, res               # повтор не допоможе
            await asyncio.sleep(3 * (attempt + 1))
        return False, res
