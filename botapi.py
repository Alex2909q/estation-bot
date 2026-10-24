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

    # ---------- фото ----------
    def _multipart_sync(self, method: str, fields: dict, file_field: str, file_bytes: bytes,
                        http_timeout: float) -> dict:
        boundary = "----estation" + os.urandom(8).hex()
        body = bytearray()
        for name, value in fields.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n").encode("utf-8")
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"photo.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode("utf-8")
        body += file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(self.base + method, data=bytes(body),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout, context=self.ssl) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return json.loads(raw)
            except ValueError:
                return {"ok": False, "error_code": e.code, "description": raw.decode("utf-8", "replace")[:300]}

    async def send_photo(self, chat_id, caption: str, photo_url: str | None = None,
                         photo_bytes: bytes | None = None, button_url: str | None = None) -> tuple[bool, dict]:
        """Фото з підписом (до 1024 символів). Якщо не вийшло — повертає False, і
        викликач надсилає звичайне текстове повідомлення."""
        markup = {"inline_keyboard": [[{"text": "Відкрити оголошення", "url": button_url}]]} if button_url else None
        res: dict = {}
        for attempt in range(3):
            try:
                if photo_bytes:
                    fields = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
                    if markup:
                        fields["reply_markup"] = json.dumps(markup)
                    res = await asyncio.to_thread(self._multipart_sync, "sendPhoto", fields, "photo",
                                                  photo_bytes, 60)
                else:
                    params = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
                    if markup:
                        params["reply_markup"] = markup
                    res = await self.call("sendPhoto", params, http_timeout=60)
            except Exception as e:
                log.warning("Бот: фото не надіслано (%s), повтор…", e)
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if res.get("ok"):
                return True, res
            if res.get("error_code") == 429:
                await asyncio.sleep(int(res.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            return False, res                    # погане фото/підпис — перейдемо на текст
        return False, res
