"""Розетка Smart Life (хмара Tuya): вхід через QR і перевірка, чи розетка на зв'язку.

Вхід такий самий, як в інтеграції Tuya для Home Assistant: «Код користувача» з застосунку
Smart Life + сканування QR у цьому ж застосунку. Акаунт розробника Tuya не потрібен.
"""
from __future__ import annotations

import io
import logging
import time

log = logging.getLogger("svitlo")

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"


def qr_png(text: str) -> bytes:
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=10, border=4)
    return buf.getvalue()


def start_login(user_code: str) -> tuple[str | None, str]:
    """→ (qr_token, повідомлення про помилку)."""
    from tuya_sharing import LoginControl
    resp = LoginControl().qr_code(CLIENT_ID, SCHEMA, user_code)
    if resp.get("success"):
        return resp["result"]["qrcode"], ""
    return None, str(resp.get("msg") or resp.get("code") or resp)


def wait_login(qr_token: str, user_code: str, timeout: int = 240) -> dict | None:
    from tuya_sharing import LoginControl
    ctl = LoginControl()
    end = time.time() + timeout
    while time.time() < end:
        try:
            ok, info = ctl.login_result(qr_token, CLIENT_ID, user_code)
            if ok:
                return info
        except Exception as e:
            log.info("Tuya: очікую сканування QR (%s)", type(e).__name__)
        time.sleep(3)
    return None


class Plug:
    """Опитування однієї розетки. Токени оновлюються автоматично й зберігаються через on_token."""

    def __init__(self, cfg: dict, on_token):
        from tuya_sharing.customerapi import CustomerApi, CustomerTokenInfo, SharingTokenListener

        class _L(SharingTokenListener):
            def update_token(self, token_info):
                on_token(token_info)

        self.cfg = cfg
        self.api = CustomerApi(CustomerTokenInfo(cfg["token"]), CLIENT_ID, cfg["user_code"],
                               cfg["endpoint"], _L())

    def devices(self) -> list[dict]:
        out = []
        homes = self.api.get("/v1.0/m/life/users/homes") or {}
        for home in homes.get("result") or []:
            res = self.api.get("/v1.0/m/life/ha/home/devices", {"homeId": str(home["ownerId"])}) or {}
            for d in res.get("result") or []:
                out.append({"id": d.get("id"), "name": d.get("name") or d.get("id"),
                            "category": d.get("category"), "online": d.get("online")})
        return out

    def online(self, device_id: str) -> bool | None:
        res = self.api.get("/v1.0/m/life/ha/devices/detail", {"devIds": device_id})
        items = (res or {}).get("result") or []
        if not items:
            return None
        return bool(items[0].get("online"))
