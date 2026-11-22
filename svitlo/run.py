#!/usr/bin/env python3
"""Бот «Світло 2.2»: актуальний графік відключень для черги 2.2 (Київщина) + стан світла вдома.

Працює безперервно: один запуск у GitHub Actions живе ~5 год 45 хв, а наступний уже стоїть у черзі
й підхоплює роботу, щойно попередній завершився (або впав).

Секрети репозиторію:
  SVITLO_BOT_TOKEN  токен бота від @BotFather (обов'язково)
  SVITLO_SESSION    рядок "api_id:api_hash:сесія" з make_session.command — читання ваших чатів і каналів
Необов'язково: SVITLO_CHAT_ID (куди слати), SVITLO_QUEUE (типово 2.2).
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import parse
import plug as plugmod
from store import Store

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Kyiv"))
QUEUE = (os.getenv("SVITLO_QUEUE") or "2.2").strip()
RUN_SECONDS = int(os.getenv("RUN_SECONDS") or 5 * 3600 + 45 * 60)
OFFICIAL_API = "https://api.github.com/repos/Baskerville42/outage-data-ua/contents/data/kyiv-region.json?ref=main"
OFFICIAL_RAW = "https://raw.githubusercontent.com/Baskerville42/outage-data-ua/main/data/kyiv-region.json"
OFFICIAL_NAME = "Сайт ДТЕК Київські регіональні електромережі"
OFFICIAL_LINK = "https://www.dtek-krem.com.ua/ua/shutdowns"
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
WORKFLOW = "svitlo.yml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("svitlo")
logging.getLogger("telethon").setLevel(logging.WARNING)


def env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def now() -> datetime:
    return datetime.now(TZ)


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def day_label(iso: str) -> str:
    d = datetime.fromisoformat(iso).date()
    today = now().date()
    rel = {0: "Сьогодні", 1: "Завтра", -1: "Вчора"}.get((d - today).days)
    base = f"{DAYS[d.weekday()]} {d:%d.%m}"
    return f"{rel}, {base}" if rel else base.capitalize()


def ago(seconds: float) -> str:
    m = int(seconds // 60)
    h, m = divmod(m, 60)
    return f"{h} год {m} хв" if h else f"{m} хв"


# ---------------- Telegram Bot API (стандартна бібліотека) ----------------

class Bot:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.ctx = ssl.create_default_context()

    def _req(self, method: str, data: bytes, ctype: str, timeout: float) -> dict:
        req = urllib.request.Request(self.base + method, data=data, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except ValueError:
                return {"ok": False, "error_code": e.code, "description": str(e)}

    async def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        for attempt in range(4):
            try:
                res = await asyncio.to_thread(self._req, method, json.dumps(params or {}).encode(),
                                              "application/json", timeout)
            except Exception as e:
                log.warning("Bot API %s: %s", method, e)
                await asyncio.sleep(2 + 3 * attempt)
                continue
            if res.get("error_code") == 429:
                await asyncio.sleep(int(res.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            return res
        return {"ok": False, "description": "network"}

    async def send(self, chat_id, text: str, buttons=None, silent=False) -> dict:
        p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
             "link_preview_options": {"is_disabled": True}, "disable_notification": silent}
        if buttons:
            p["reply_markup"] = {"inline_keyboard": buttons}
        res = await self.call("sendMessage", p)
        if not res.get("ok"):
            log.warning("Не надіслано: %s", res.get("description"))
        return res

    async def send_photo(self, chat_id, png: bytes, caption: str) -> dict:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="qr.png"\r\n'
                     f'Content-Type: image/png\r\n\r\n'.encode() + png + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return await asyncio.to_thread(self._req, "sendPhoto", b"".join(parts),
                                       f"multipart/form-data; boundary={boundary}", 60)


# ---------------- Основна логіка ----------------

class App:
    def __init__(self):
        self.token = env("SVITLO_BOT_TOKEN")
        if not self.token:
            raise SystemExit("Немає секрету SVITLO_BOT_TOKEN — створіть бота в @BotFather і додайте токен.")
        self.bot = Bot(self.token)
        self.store = Store(self.token, REPO)
        st = self.store.load()
        for k, v in {"owner": None, "offset": None, "pin_id": None, "pin_text": "", "best": {},
                     "announced": {}, "chats": {}, "pending": {}, "official": {}, "tuya": None,
                     "plug": {"online": None, "since": None}, "welcomed": False, "diag": {},
                     "test_done": ""}.items():
            st.setdefault(k, v)
        self.st = st
        self.started = time.time()
        self.deadline = self.started + RUN_SECONDS
        self.client = None
        self.me_id = None
        self.seen: set[str] = set()
        self.plug = None
        self.plug_errors = 0
        self.plug_err_notified = False
        self.login_busy = False
        self.lock = asyncio.Lock()

    def diag(self, key: str, value):
        self.st["diag"][key] = f"{now():%d.%m %H:%M} {value}"[:300]

    @property
    def owner(self):
        return env("SVITLO_CHAT_ID") or self.st["owner"]

    # ---------- графіки ----------

    def offer(self, iso: str, iv: list, pub: float, src: str, link: str | None, kind: str) -> bool:
        """Новий варіант графіка на дату. Перемагає найсвіжіше опублікований. → чи змінився найкращий."""
        if iso < (now().date() - timedelta(days=1)).isoformat():
            return False
        cur = self.st["best"].get(iso)
        rec = {"iv": iv, "pub": pub, "src": src, "link": link, "kind": kind}
        if cur and cur["pub"] > pub:
            return False
        if cur and cur["pub"] == pub and cur["iv"] == iv:
            return False
        self.st["best"][iso] = rec
        return True

    async def after_change(self, dates: set[str]):
        today = now().date().isoformat()
        for iso in sorted(dates):
            if iso < today:
                continue
            rec = self.st["best"][iso]
            prev = self.st["announced"].get(iso)
            if prev == rec["iv"]:
                continue
            self.st["announced"][iso] = rec["iv"]
            if self.owner and self.st["welcomed"]:
                lines = [f"⚡ <b>{'Новий' if prev is None else 'Змінено'} графік · черга {QUEUE}</b>",
                         f"<b>{day_label(iso)}</b>", parse.fmt_intervals(rec["iv"])]
                if rec["iv"]:
                    lines.append(f"Без світла: {parse.total_off(rec['iv'])}")
                if prev:
                    lines.append(f"<i>Було: {', '.join(parse.hm(a) + '–' + parse.hm(b) for a, b in prev)}</i>")
                elif prev == [] and rec["iv"]:
                    lines.append("<i>Було: без відключень</i>")
                lines.append(self.src_line(rec))
                await self.bot.send(self.owner, "\n".join(lines))
        await self.refresh_pin()

    def src_line(self, rec: dict) -> str:
        t = datetime.fromtimestamp(rec["pub"], TZ)
        when = f"{t:%H:%M}" if t.date() == now().date() else f"{t:%d.%m %H:%M}"
        name = esc(rec["src"])
        if rec.get("link"):
            name = f'<a href="{esc(rec["link"])}">{name}</a>'
        return f"Джерело: {name}, {when}"

    def now_line(self) -> str:
        n = now()
        mins = n.hour * 60 + n.minute
        today = self.st["best"].get(n.date().isoformat())
        tomorrow = self.st["best"].get((n.date() + timedelta(days=1)).isoformat())
        if not today:
            return "💡 На сьогодні графіка немає"
        for a, b in today["iv"]:
            if a <= mins < b:
                return f"💡 Зараз за графіком: 🔴 світла немає до {parse.hm(b)}"
        nxt = next((a for a, b in today["iv"] if a > mins), None)
        if nxt is not None:
            return f"💡 Зараз за графіком: 🟢 світло є, відключення о {parse.hm(nxt)}"
        if tomorrow and tomorrow["iv"]:
            return f"💡 Зараз за графіком: 🟢 світло є, завтра відключення о {parse.hm(tomorrow['iv'][0][0])}"
        return "💡 Зараз за графіком: 🟢 світло є, сьогодні відключень більше немає"

    def plug_line(self) -> str | None:
        if not (self.st["tuya"] and self.st["tuya"].get("device_id")):
            return None
        p = self.st["plug"]
        if p["online"] is None:
            return "🔌 Розетка: стан ще невідомий"
        since = datetime.fromtimestamp(p["since"], TZ).strftime("%H:%M") if p["since"] else "?"
        return f"🔌 Вдома: {'🟢 світло є' if p['online'] else '🔴 світла немає'} (з {since})"

    def schedule_text(self) -> str:
        n = now()
        out = [f"📅 <b>Черга {QUEUE} · Київщина</b>", ""]
        for i in (0, 1):
            iso = (n.date() + timedelta(days=i)).isoformat()
            rec = self.st["best"].get(iso)
            out.append(f"<b>{day_label(iso)}</b>")
            if rec:
                out.append(parse.fmt_intervals(rec["iv"]))
                if rec["iv"]:
                    out.append(f"Без світла: {parse.total_off(rec['iv'])}")
                out.append(f"<i>{self.src_line(rec)}</i>")
            else:
                out.append("Графіка ще немає")
            out.append("")
        out.append(self.now_line())
        pl = self.plug_line()
        if pl:
            out.append(pl)
        out.append(f"\n<i>Оновлено {n:%H:%M}</i>")
        return "\n".join(out)

    async def refresh_pin(self, force_new: bool = False):
        if not self.owner:
            return
        text = self.schedule_text()
        body = re.sub(r"Оновлено \d\d:\d\d", "", text)
        if not force_new and self.st["pin_id"] and body == re.sub(r"Оновлено \d\d:\d\d", "", self.st["pin_text"]):
            return
        if self.st["pin_id"] and not force_new:
            res = await self.bot.call("editMessageText", {
                "chat_id": self.owner, "message_id": self.st["pin_id"], "text": text, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True}})
            if res.get("ok") or "not modified" in str(res.get("description", "")):
                self.st["pin_text"] = text
                return
        res = await self.bot.send(self.owner, text, silent=True)
        if res.get("ok"):
            mid = res["result"]["message_id"]
            await self.bot.call("unpinAllChatMessages", {"chat_id": self.owner})
            await self.bot.call("pinChatMessage", {"chat_id": self.owner, "message_id": mid,
                                                   "disable_notification": True})
            self.st["pin_id"], self.st["pin_text"] = mid, text

    # ---------- офіційне джерело ----------

    def _fetch_official(self) -> dict | None:
        tok = env("GITHUB_TOKEN")
        attempts = [(OFFICIAL_API, {"Accept": "application/vnd.github.raw", **({"Authorization": f"Bearer {tok}"} if tok else {})}),
                    (OFFICIAL_RAW, {})]
        for url, hdr in attempts:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "svitlo-bot", **hdr})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except Exception as e:
                log.info("Офіційні дані (%s): %s", url.split("/")[2], e)
        return None

    async def official_loop(self):
        while True:
            data = await asyncio.to_thread(self._fetch_official)
            self.diag("official", "ok" if data else "помилка завантаження")
            if data:
                dates, updated = parse.parse_official(data, TZ, QUEUE)
                changed = set()
                async with self.lock:
                    for iso, iv in dates.items():
                        prev = self.st["official"].get(iso)
                        pub = updated.timestamp() if updated else time.time()
                        if prev and prev["iv"] != iv and pub <= prev["pub"]:
                            pub = time.time()        # зміст змінився, а час на сайті ні
                        if prev and prev["iv"] == iv:
                            pub = prev["pub"]
                        self.st["official"][iso] = {"iv": iv, "pub": pub}
                        if self.offer(iso, iv, pub, OFFICIAL_NAME, OFFICIAL_LINK, "official"):
                            changed.add(iso)
                    if changed:
                        await self.after_change(changed)
                await self.check_attention(data.get("attention"))
            await asyncio.sleep(60)

    async def check_attention(self, att):
        """Віконце «Шановні клієнти!» на сайті ДТЕК (екстрені відключення тощо)."""
        text = (att or {}).get("text") if isinstance(att, dict) else (att if isinstance(att, str) else None)
        text = (text or "").strip()
        if not (self.owner and self.st["welcomed"]):
            return
        h = hashlib.sha256(text.encode()).hexdigest()[:16] if text else ""
        if h == self.st.get("attention", ""):
            return
        self.st["attention"] = h
        if text:
            await self.bot.send(self.owner, f"📢 <b>Повідомлення на сайті ДТЕК (Київщина)</b>\n\n{esc(text[:3500])}")

    # ---------- Telegram-акаунт: чати й канали ----------

    async def start_account(self):
        raw = env("SVITLO_SESSION")
        if not raw:
            log.warning("SVITLO_SESSION не задано — читаю лише офіційні дані.")
            self.diag("account", "SVITLO_SESSION не задано")
            return
        try:
            api_id, api_hash, sess = raw.split(":", 2)
            from telethon import TelegramClient, events
            from telethon.sessions import StringSession
            self.client = TelegramClient(StringSession(sess), int(api_id), api_hash,
                                         device_model="Svitlo GitHub", receive_updates=True)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise RuntimeError("сесія недійсна")
            me = await self.client.get_me()
            self.me_id = me.id
            if not self.st["owner"]:
                self.st["owner"] = me.id
            await self.client.get_dialogs(limit=None)
            self.client.add_event_handler(self.on_event, events.NewMessage())
            self.client.add_event_handler(self.on_event, events.MessageEdited())
            log.info("Акаунт Telegram підключено, читаю чати й канали.")
            self.diag("account", f"ok, акаунт {me.id}")
        except Exception as e:
            log.error("Telegram-акаунт: %s", e)
            self.diag("account", f"помилка: {e}")
            self.client = None
            if self.owner:
                await self.bot.send(self.owner, f"⚠️ Не вдалося підключити ваш Telegram-акаунт ({esc(e)}). "
                                                "Графіки з чатів не читаються, лише з сайту ДТЕК. "
                                                "Створіть SVITLO_SESSION заново через make_session.command.")

    async def on_event(self, event):
        try:
            if event.is_private:
                return
            chat = await event.get_chat()
            await self.handle_message(chat, event.message)
        except Exception as e:
            log.warning("Обробка повідомлення: %s", e)

    def link_for(self, chat, msg_id: int) -> str | None:
        uname = getattr(chat, "username", None)
        if uname:
            return f"https://t.me/{uname}/{msg_id}"
        if getattr(chat, "megagroup", False) or getattr(chat, "broadcast", False):
            return f"https://t.me/c/{chat.id}/{msg_id}"
        return None

    async def handle_message(self, chat, msg):
        text = getattr(msg, "message", "") or ""
        if len(text) < 8 or not (QUEUE in text or "черг" in text.lower() or parse.NO_OUTAGE.search(text)):
            return
        from telethon import utils
        cid = str(utils.get_peer_id(chat))
        edit = getattr(msg, "edit_date", None)
        pub_dt = (edit or msg.date).astimezone(TZ)
        key = f"{cid}:{msg.id}:{int(pub_dt.timestamp())}"
        if key in self.seen:
            return
        self.seen.add(key)
        title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or cid
        info = self.st["chats"].setdefault(cid, {"title": title, "trusted": None, "last_id": 0})
        info["title"] = title
        info["last_id"] = max(int(info.get("last_id") or 0), msg.id)
        if info.get("trusted") is False:
            return
        link = self.link_for(chat, msg.id)
        res = parse.parse_text(text, pub_dt, QUEUE, trusted=bool(info.get("trusted")))
        changed = set()
        async with self.lock:
            if res["dates"]:
                info["sched_at"] = pub_dt.timestamp()
                if res["region"] == "kr":
                    info["kr_at"] = pub_dt.timestamp()
                for iso, iv in res["dates"].items():
                    if self.offer(iso, iv, pub_dt.timestamp(), title, link, "chat"):
                        changed.add(iso)
                if changed:
                    log.info("Новий графік з «%s»: %s", title, res["dates"])
                    await self.after_change(changed)
            elif info.get("trusted") is None and res["region"] != "other":
                alt = parse.parse_text(text, pub_dt, QUEUE, trusted=True)
                if alt["dates"]:
                    pend = self.st["pending"].setdefault(cid, [])
                    pend.append({"text": text[:3000], "ts": pub_dt.timestamp(), "link": link})
                    del pend[:-5]
                    if not info.get("asked") and self.owner:
                        info["asked"] = True
                        sample = esc(text[:400])
                        await self.bot.send(
                            self.owner,
                            f"🔎 У чаті <b>{esc(title)}</b> публікують графіки для {QUEUE}, але без назви регіону:\n\n"
                            f"<i>{sample}</i>\n\nЦе чат вашого населеного пункту на Київщині? Брати графіки звідси?",
                            buttons=[[{"text": "✅ Так, брати", "callback_data": f"src:1:{cid}"},
                                      {"text": "❌ Ні", "callback_data": f"src:0:{cid}"}]])

    async def apply_trust(self, cid: str, ok: bool):
        info = self.st["chats"].get(cid)
        if not info:
            return
        info["trusted"] = ok
        changed = set()
        if ok:
            for p in self.st["pending"].pop(cid, []):
                res = parse.parse_text(p["text"], datetime.fromtimestamp(p["ts"], TZ), QUEUE, trusted=True)
                for iso, iv in res["dates"].items():
                    if self.offer(iso, iv, p["ts"], info["title"], p.get("link"), "chat"):
                        changed.add(iso)
        else:
            self.st["pending"].pop(cid, None)
        if changed:
            await self.after_change(changed)

    async def discovery(self, hours: int):
        """Пошук по всіх ваших чатах і каналах: де публікують графіки для черги."""
        if not self.client:
            return
        since = now() - timedelta(hours=hours)
        for q in (QUEUE, "черга", "графік відключень"):
            try:
                async for m in self.client.iter_messages(None, search=q, limit=300):
                    if m.date.astimezone(TZ) < since:
                        break
                    if m.is_private:
                        continue
                    chat = await m.get_chat()
                    if chat is not None:
                        await self.handle_message(chat, m)
            except Exception as e:
                log.warning("Пошук «%s»: %s", q, e)
            await asyncio.sleep(3)

    async def discovery_loop(self):
        await self.discovery(48)
        while True:
            await asyncio.sleep(15 * 60)
            await self.discovery(3)

    async def poll_loop(self):
        """Додаткове опитування відомих джерел — якщо Telegram не надіслав оновлення миттєво."""
        while True:
            await asyncio.sleep(90)
            if not self.client:
                continue
            week = time.time() - 7 * 86400
            srcs = [(cid, i) for cid, i in self.st["chats"].items()
                    if i.get("trusted") or (i.get("trusted") is not False and (i.get("kr_at") or 0) > week)]
            srcs.sort(key=lambda x: -(x[1].get("sched_at") or 0))
            for cid, info in srcs[:30]:
                try:
                    ent = await self.client.get_entity(int(cid))
                    msgs = await self.client.get_messages(ent, limit=20, min_id=int(info.get("last_id") or 0))
                    for m in reversed(msgs):
                        await self.handle_message(ent, m)
                except Exception as e:
                    log.info("Опитування %s: %s", info.get("title"), e)
                await asyncio.sleep(1)

    # ---------- розетка ----------

    def _save_token(self, token_info: dict):
        if self.st["tuya"]:
            self.st["tuya"]["token"] = token_info

    async def plug_loop(self):
        while True:
            t = self.st["tuya"]
            if t and t.get("device_id") and self.owner:
                try:
                    if self.plug is None:
                        self.plug = plugmod.Plug(t, self._save_token)
                    online = await asyncio.to_thread(self.plug.online, t["device_id"])
                    if online is None:
                        raise RuntimeError("немає даних про пристрій")
                    self.plug_errors = 0
                    self.diag("plug", f"ok, online={online}")
                    if self.plug_err_notified:
                        self.plug_err_notified = False
                        await self.bot.send(self.owner, "🔌 Зв'язок із хмарою Smart Life відновлено.", silent=True)
                    await self.plug_state(online)
                except Exception as e:
                    self.plug_errors += 1
                    self.plug = None
                    log.warning("Розетка: %s", e)
                    self.diag("plug", f"помилка: {e}")
                    if self.plug_errors == 10 and not self.plug_err_notified:
                        self.plug_err_notified = True
                        await self.bot.send(self.owner, "⚠️ 10 хвилин не можу отримати стан розетки з хмари Smart Life. "
                                            f"Якщо це триває, увійдіть ще раз: <code>/rozetka КОД</code> ({esc(e)})")
            await asyncio.sleep(60)

    async def plug_state(self, online: bool):
        p = self.st["plug"]
        prev = p["online"]
        ts = time.time()
        if prev == online:
            return
        p["online"], old_since, p["since"] = online, p["since"], ts
        if prev is None:
            await self.refresh_pin()
            return
        n = now()
        lasted = f" Не було {ago(ts - old_since)}." if online and old_since else ""
        if online:
            text = f"🟢 <b>Світло з'явилося</b> о {n:%H:%M}.{lasted}"
        else:
            mins = n.hour * 60 + n.minute
            today = self.st["best"].get(n.date().isoformat())
            plan = None
            if today:
                plan = next(((a, b) for a, b in today["iv"] if a - 15 <= mins < b), None)
            note = (f"\nЗа графіком відключення {parse.hm(plan[0])}–{parse.hm(plan[1])}." if plan
                    else "\nЗа графіком зараз світло мало бути — схоже на аварійне відключення.")
            on_for = f" Світло було {ago(ts - old_since)}." if old_since else ""
            text = f"🔴 <b>Світло зникло</b> ≈ о {n:%H:%M}.{on_for}{note}"
        await self.bot.send(self.owner, text)
        await self.refresh_pin()

    async def tuya_login(self, chat_id, code: str):
        if self.login_busy:
            await self.bot.send(chat_id, "Вхід уже триває — відскануйте QR з попереднього повідомлення.")
            return
        self.login_busy = True
        try:
            qr, err = await asyncio.to_thread(plugmod.start_login, code)
            self.diag("tuya_login", "QR отримано" if qr else f"код не прийнято: {err}")
            if not qr:
                await self.bot.send(chat_id, f"❌ Smart Life не прийняв код користувача: {esc(err)}")
                return
            png = plugmod.qr_png(f"tuyaSmart--qrLogin?token={qr}")
            await self.bot.send_photo(
                chat_id, png,
                "📷 Відскануйте цей QR у застосунку <b>Smart Life</b>: «+» вгорі праворуч → «Сканувати».\n"
                "QR треба показати з іншого екрана (ноутбук, інший телефон), або збережіть фото й виберіть його "
                "в сканері з галереї. Чекаю 4 хвилини.")
            info = await asyncio.to_thread(plugmod.wait_login, qr, code)
            self.diag("tuya_login", "вхід успішний" if info else "QR не відскановано за 4 хв")
            if not info:
                await self.bot.send(chat_id, "⌛ QR не відскановано. Надішліть <code>/rozetka КОД</code> ще раз.")
                return
            cfg = {"user_code": code, "endpoint": info.get("endpoint"), "terminal_id": info.get("terminal_id"),
                   "token": {k: info.get(k) for k in ("t", "uid", "expire_time", "access_token", "refresh_token")}}
            self.st["tuya"] = cfg
            self.plug = plugmod.Plug(cfg, self._save_token)
            devices = await asyncio.to_thread(self.plug.devices)
            if not devices:
                await self.bot.send(chat_id, "Увійшов, але в акаунті Smart Life не знайшов пристроїв.")
                return
            self.st["tuya"]["devices"] = {d["id"]: d["name"] for d in devices}
            plugs = [d for d in devices if d.get("category") in ("cz", "pc", "kg")] or devices
            if len(plugs) == 1:
                await self.choose_device(chat_id, plugs[0]["id"])
            else:
                await self.bot.send(chat_id, "✅ Увійшов. Яку розетку відстежувати?",
                                    buttons=[[{"text": d["name"], "callback_data": f"dev:{d['id']}"}] for d in plugs])
        except Exception as e:
            log.exception("Вхід Smart Life")
            self.diag("tuya_login", f"виняток: {e}")
            await self.bot.send(chat_id, f"❌ Помилка входу Smart Life: {esc(e)}")
        finally:
            self.login_busy = False

    async def choose_device(self, chat_id, dev_id: str):
        t = self.st["tuya"]
        if not t:
            return
        t["device_id"] = dev_id
        name = (t.get("devices") or {}).get(dev_id, dev_id)
        self.st["plug"] = {"online": None, "since": None}
        await self.bot.send(chat_id, f"✅ Відстежую розетку «{esc(name)}». Перевірка щохвилини: "
                                     "напишу, коли світло зникне або з'явиться.")

    # ---------- команди бота ----------

    async def updates_loop(self):
        while True:
            params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if self.st["offset"]:
                params["offset"] = self.st["offset"]
            res = await self.bot.call("getUpdates", params, timeout=40)
            if not res.get("ok"):
                if res.get("error_code") == 409:      # попередній запуск ще дочитує
                    await asyncio.sleep(5)
                else:
                    await asyncio.sleep(5)
                continue
            for upd in res["result"]:
                self.st["offset"] = upd["update_id"] + 1
                try:
                    await self.handle_update(upd)
                except Exception:
                    log.exception("Обробка команди")

    def is_owner(self, uid) -> bool:
        return not self.owner or str(uid) == str(self.owner)

    async def handle_update(self, upd: dict):
        if "callback_query" in upd:
            cq = upd["callback_query"]
            uid = cq["from"]["id"]
            await self.bot.call("answerCallbackQuery", {"callback_query_id": cq["id"]})
            if not self.is_owner(uid):
                return
            data = cq.get("data", "")
            if data.startswith("src:"):
                _, ok, cid = data.split(":", 2)
                await self.apply_trust(cid, ok == "1")
                title = self.st["chats"].get(cid, {}).get("title", cid)
                await self.bot.send(uid, f"{'✅ Беру' if ok == '1' else '❌ Не беру'} графіки з «{esc(title)}».", silent=True)
            elif data.startswith("dev:"):
                await self.choose_device(uid, data[4:])
            return
        msg = upd.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        uid = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or msg.get("chat", {}).get("type") != "private":
            return
        if not self.is_owner(uid):
            await self.bot.send(chat_id, "Це особистий бот.")
            return
        if not self.st["owner"]:
            self.st["owner"] = uid
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd == "/start" or not self.st["welcomed"]:
            self.st["welcomed"] = True
            await self.bot.send(chat_id,
                f"👋 Стежу за графіком відключень для черги <b>{QUEUE}</b> (Київщина): сайт ДТЕК і ваші чати й канали. "
                "Закріплене повідомлення завжди показує найсвіжіший графік, а при змінах я напишу окремо.\n\n"
                "/grafik — графік на сьогодні й завтра\n/dzherela — звідки беру графіки\n"
                "/rozetka КОД — підключити розетку Smart Life (світло є / немає)\n/svitlo — стан світла вдома\n"
                "/status — стан бота\n/test — приклади сповіщень")
            for iso in self.st["best"]:
                self.st["announced"][iso] = self.st["best"][iso]["iv"]
            await self.refresh_pin(force_new=True)
            return
        if cmd in ("/grafik", "/графік", "графік"):
            await self.bot.send(chat_id, self.schedule_text())
            await self.refresh_pin()
        elif cmd == "/dzherela":
            await self.bot.send(chat_id, self.sources_text())
        elif cmd == "/rozetka":
            code = arg.strip()
            if not code:
                await self.bot.send(chat_id, "Надішліть <code>/rozetka КОД</code>. Код: Smart Life → Я → ⚙️ → "
                                             "Обліковий запис і безпека → Код користувача.")
            else:
                asyncio.create_task(self.tuya_login(chat_id, code))
        elif cmd == "/status":
            await self.bot.send(chat_id, self.status_text())
        elif cmd == "/test":
            await self.send_test(chat_id)
        elif cmd == "/svitlo":
            await self.bot.send(chat_id, self.plug_line() or "Розетку ще не підключено: /rozetka КОД")
        else:
            await self.bot.send(chat_id, "Команди: /grafik, /dzherela, /rozetka КОД, /svitlo", silent=True)

    def status_text(self) -> str:
        d = self.st["diag"]
        acc = "🟢 підключено" if self.client else "⚪ не підключено (немає SVITLO_SESSION)"
        if not self.client and "помилка" in d.get("account", ""):
            acc = "🔴 " + d["account"]
        t = self.st["tuya"]
        if t and t.get("device_id"):
            pl = self.plug_line() or ""
        elif t:
            pl = "🟡 вхід виконано, розетку не вибрано"
        else:
            pl = "⚪ не підключено — надішліть /rozetka КОД"
        up = ago(time.time() - self.started)
        return ("🛠 <b>Стан бота</b>\n\n"
                f"Працює без перерви: {up}\n"
                f"🏢 Сайт ДТЕК: {'🟢 дані отримано' if self.st['official'] else '🔴 немає даних'}\n"
                f"💬 Ваші чати: {acc}\n"
                f"📍 Джерел у чатах: {sum(1 for i in self.st['chats'].values() if i.get('sched_at'))}\n"
                f"🔌 Розетка: {pl}")

    async def send_test(self, chat_id):
        await self.bot.send(chat_id, "🧪 <b>ТЕСТ.</b> Нижче приклади повідомлень, які я надсилатиму. "
                                     "Дані в них вигадані, справжній графік — у закріпленому повідомленні.", silent=True)
        await self.bot.send(chat_id, "🧪 ТЕСТ\n⚡ <b>Змінено графік · черга 2.2</b>\n<b>Завтра</b>\n🔴 10:00–14:00\n"
                                     "🔴 18:00–20:00\nБез світла: 6 год\n<i>Було: 10:00–14:00</i>\nДжерело: Чат вашого селища, 21:47")
        await self.bot.send(chat_id, "🧪 ТЕСТ\n🔴 <b>Світло зникло</b> ≈ о 18:02. Світло було 4 год 2 хв.\n"
                                     "За графіком відключення 18:00–20:00.")
        await self.bot.send(chat_id, "🧪 ТЕСТ\n🟢 <b>Світло з'явилося</b> о 19:56. Не було 1 год 54 хв.")
        await self.bot.send(chat_id, self.status_text())

    def sources_text(self) -> str:
        out = [f"🔎 <b>Джерела графіків для {QUEUE}</b>", ""]
        off = self.st["official"]
        out.append(f"🏢 {OFFICIAL_NAME}: " + ("дані є" if off else "ще не отримано"))
        if not self.client:
            out.append("⚠️ Ваш Telegram-акаунт не підключено — чати й канали не читаються.")
        rows = sorted(self.st["chats"].items(), key=lambda x: -(x[1].get("sched_at") or 0))
        good = [(c, i) for c, i in rows if i.get("sched_at") and i.get("trusted") is not False]
        if good:
            out.append("\n<b>Чати й канали з графіками:</b>")
            for _, i in good[:20]:
                t = datetime.fromtimestamp(i["sched_at"], TZ)
                mark = "📍" if i.get("trusted") else "📰"
                out.append(f"{mark} {esc(i['title'])} — останній {t:%d.%m %H:%M}")
        asked = [i for c, i in rows if i.get("trusted") is None and i.get("asked")]
        if asked:
            out.append("\nОчікують вашої відповіді: " + ", ".join(esc(i["title"]) for i in asked[:10]))
        no = [i for c, i in rows if i.get("trusted") is False]
        if no:
            out.append("\nВідхилені: " + ", ".join(esc(i["title"]) for i in no[:10]))
        out.append("\n📍 — місцевий чат (ви підтвердили), 📰 — пост із назвою «Київщина».\n"
                   "Перемагає графік, опублікований найпізніше.")
        return "\n".join(out)

    # ---------- службове ----------

    async def housekeeping(self):
        while True:
            await asyncio.sleep(60)
            cutoff = (now().date() - timedelta(days=2)).isoformat()
            for k in ("best", "announced", "official"):
                for iso in [d for d in self.st[k] if d < cutoff]:
                    del self.st[k][iso]
            if len(self.seen) > 20000:
                self.seen.clear()
            await self.refresh_pin()
            await asyncio.to_thread(self.store.save, self.st)

    async def ensure_successor(self):
        """Через 2 хв стабільної роботи ставимо наступний запуск у чергу (він стартує, щойно цей завершиться)."""
        await asyncio.sleep(120)
        Path("/tmp/svitlo_healthy").write_text("1")
        repo, tok = env("GITHUB_REPOSITORY"), env("GITHUB_TOKEN")
        if not (repo and tok):
            return

        def gh(method, path, body=None):
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", method=method,
                                         data=json.dumps(body).encode() if body else None,
                                         headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                                  "User-Agent": "svitlo-bot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}

        while True:
            try:
                runs = await asyncio.to_thread(gh, "GET", f"actions/workflows/{WORKFLOW}/runs?per_page=10")
                waiting = [r for r in runs.get("workflow_runs", [])
                           if r["status"] in ("queued", "waiting", "pending", "requested")]
                if not waiting:
                    await asyncio.to_thread(gh, "POST", f"actions/workflows/{WORKFLOW}/dispatches", {"ref": "main"})
                    log.info("Наступний запуск поставлено в чергу.")
            except Exception as e:
                log.warning("Черга наступного запуску: %s", e)
            await asyncio.sleep(20 * 60)

    async def main(self):
        me = await self.bot.call("getMe")
        if not me.get("ok"):
            raise SystemExit(f"Токен бота не працює: {me.get('description')}")
        log.info("Бот @%s запущено, черга %s", me["result"]["username"], QUEUE)
        await self.bot.call("setMyCommands", {"commands": [
            {"command": "grafik", "description": "Графік на сьогодні й завтра"},
            {"command": "svitlo", "description": "Чи є світло вдома (розетка)"},
            {"command": "dzherela", "description": "Звідки беру графіки"},
            {"command": "rozetka", "description": "Підключити розетку Smart Life: /rozetka КОД"},
            {"command": "status", "description": "Стан бота і підключень"},
            {"command": "test", "description": "Приклади сповіщень"}]})
        await self.start_account()
        marker = BASE / "test_once.txt"
        if marker.exists() and self.owner and self.st["welcomed"]:
            tid = marker.read_text().strip()
            if tid and tid != self.st["test_done"]:
                self.st["test_done"] = tid
                await self.send_test(self.owner)
        tasks = [self.updates_loop(), self.official_loop(), self.plug_loop(), self.housekeeping(),
                 self.ensure_successor()]
        if self.client:
            tasks += [self.discovery_loop(), self.poll_loop()]
        runner = asyncio.gather(*tasks)
        try:
            await asyncio.wait_for(runner, timeout=max(60, self.deadline - time.time()))
        except asyncio.TimeoutError:
            log.info("Час цього запуску вичерпано — передаю роботу наступному.")
        finally:
            await asyncio.to_thread(self.store.save, self.st)
            if self.client:
                await self.client.disconnect()


if __name__ == "__main__":
    asyncio.run(App().main())
