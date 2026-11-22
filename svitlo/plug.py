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
        """Список пристроїв дому показує актуальний online (поле в /devices/detail запізнюється)."""
        if not getattr(self, "_homes", None):
            homes = self.api.get("/v1.0/m/life/users/homes") or {}
            self._homes = [str(h["ownerId"]) for h in homes.get("result") or []]
        for home in self._homes:
            res = self.api.get("/v1.0/m/life/ha/home/devices", {"homeId": home}) or {}
            for d in res.get("result") or []:
                if d.get("id") == device_id:
                    return bool(d.get("online"))
        return None


class PlugEvents:
    """Миттєві події від хмари Tuya (MQTT) — так само, як їх отримує застосунок Smart Life.

    on_online(bool, причина) викликається з потоку MQTT.
    """

    def __init__(self, cfg: dict, on_token, on_online):
        from tuya_sharing import Manager
        from tuya_sharing.customerapi import SharingTokenListener

        class _L(SharingTokenListener):
            def update_token(self, token_info):
                on_token(token_info)

        self.cfg = cfg
        self.dev = cfg["device_id"]
        self.on_online = on_online
        self.manager = Manager(CLIENT_ID, cfg["user_code"], cfg.get("terminal_id") or "", cfg["endpoint"],
                               cfg["token"], _L())
        self.initial = None
        self.last_event = 0.0

    def start(self):
        self.manager.update_device_cache()
        dev = self.manager.device_map.get(self.dev)
        self.initial = None if dev is None else bool(dev.online)
        self.manager.refresh_mq()
        self.manager.mq.add_message_listener(self._msg)

    def alive(self) -> bool:
        mq = self.manager.mq
        return bool(mq and mq.is_alive())

    def _msg(self, msg: dict):
        try:
            data = msg.get("data") or {}
            proto = msg.get("protocol")
            if proto == 20 and data.get("bizCode") in ("online", "offline"):
                if (data.get("bizData") or {}).get("devId") == self.dev:
                    self.last_event = time.time()
                    self.on_online(data["bizCode"] == "online", "подія " + data["bizCode"])
            elif proto == 4 and data.get("devId") == self.dev:
                self.last_event = time.time()
                self.on_online(True, "звіт пристрою")
        except Exception as e:
            log.warning("Tuya MQ: %s", e)

    def stop(self):
        try:
            if self.manager.mq:
                self.manager.mq.stop()
        except Exception:
            pass
