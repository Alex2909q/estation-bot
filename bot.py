#!/usr/bin/env python3
"""Моніторинг оголошень про зарядні станції в Telegram-групах.

Як працює:
  * ваш власний акаунт Telegram (через Telethon) раз на CHECK_INTERVAL секунд
    читає нові повідомлення у вказаних групах — лише читає, нічого туди не пише;
  * якщо в повідомленні є ключове слово з keywords.txt, ваш бот надсилає
    вам сповіщення з текстом оголошення і прямим посиланням на нього.

Звичайний бот не може читати чужі групи (лише ті, де він адміністратор),
тому читання йде через ваш акаунт, а сповіщення — через бота.
"""
from __future__ import annotations

import asyncio
import getpass
import html
import json
import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, User

import core
from botapi import BotAPI

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        sys.exit(f"❌ У .env параметр {name} має бути числом, а зараз: {raw!r}")


def _timezone():
    from zoneinfo import ZoneInfo
    for name in (os.getenv("TIMEZONE", "").strip() or "Europe/Kyiv", "Europe/Kiev"):
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return None  # системний час


API_ID = _env_int("API_ID", 0)
API_HASH = (os.getenv("API_HASH") or "").strip()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
NOTIFY_CHAT_ID = (os.getenv("NOTIFY_CHAT_ID") or "").strip()
def _load_groups() -> list[str]:
    raw = (os.getenv("GROUPS") or "").strip()
    if raw:
        items = raw.replace("\n", ",").split(",")
    elif (BASE / "channels.txt").exists():   # спільний список із версією для GitHub
        items = [l.split("#")[0] for l in (BASE / "channels.txt").read_text(encoding="utf-8").splitlines()]
    else:
        items = []
    return [i.strip() for i in items if i.strip()]


GROUPS = _load_groups()
CHECK_INTERVAL = max(15, _env_int("CHECK_INTERVAL", 60))
INITIAL_LOOKBACK = max(0, _env_int("INITIAL_LOOKBACK", 0))
DEDUP_MINUTES = max(0, _env_int("DEDUP_MINUTES", 30))
TZ = _timezone()

STATE_FILE = BASE / "state.json"
SESSION_FILE = str(BASE / "user")          # створиться user.session — це ключ до акаунта, не передавайте нікому
KEYWORDS_FILE = BASE / "keywords.txt"
EXCLUDE_FILE = BASE / "exclude.txt"

log = logging.getLogger("monitor")


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = RotatingFileHandler(BASE / "monitor.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[console, file])
    logging.getLogger("telethon").setLevel(logging.WARNING)


def now_local() -> datetime:
    return datetime.now(TZ) if TZ else datetime.now()


# ---------------------------------------------------------------- Monitor ---

class Monitor:
    def __init__(self) -> None:
        self.keywords = core.load_keywords(KEYWORDS_FILE)
        self.excludes = core.load_keywords(EXCLUDE_FILE)
        self.state = self._load_state()
        self.groups: list[dict] = []
        self.pending: list[tuple[str, str | None]] = []
        self.paused = False
        self.last_check: datetime | None = None
        self.found_session = 0
        self.wake: asyncio.Event | None = None
        self.bot = BotAPI(BOT_TOKEN)
        self.bot_username = ""
        self.client: TelegramClient | None = None
        self.notify_chat = NOTIFY_CHAT_ID

    # ---------- стан ----------
    @staticmethod
    def _load_state() -> dict:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault("last_ids", {})
        data.setdefault("recent", {})
        data.pop("seen", None)
        data.setdefault("found_total", 0)
        return data

    def save_state(self) -> None:
        cutoff = time.time() - max(DEDUP_MINUTES * 4 * 60, 3 * 3600)
        self.state["recent"] = {k: v for k, v in self.state["recent"].items() if v >= cutoff}
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)

    # ---------- запуск ----------
    def check_config(self) -> None:
        problems = []
        if not API_ID or not API_HASH:
            problems.append("API_ID / API_HASH (беруться на https://my.telegram.org → API development tools)")
        if not BOT_TOKEN:
            problems.append("BOT_TOKEN (видає @BotFather після команди /newbot)")
        if not GROUPS:
            problems.append("GROUPS (посилання або @юзернейми груп через кому)")
        if not self.keywords:
            problems.append("keywords.txt порожній — додайте ключові слова")
        if problems:
            sys.exit("❌ Не заповнено налаштування в .env:\n  • " + "\n  • ".join(problems))

    async def resolve_groups(self) -> None:
        if any(isinstance(core.parse_group_ref(g), int) for g in GROUPS):
            await self.client.get_dialogs()          # щоб Telethon знав числові ID
        for ref in GROUPS:
            target = core.parse_group_ref(ref)
            try:
                entity = await self.client.get_entity(target)
            except Exception as e:
                log.error("❌ Не вдалося знайти групу %r: %s", ref, e)
                continue
            if isinstance(entity, User):
                log.error("❌ %r — це користувач або бот, а не група", ref)
                continue
            self.groups.append({
                "ref": ref,
                "entity": entity,
                "key": str(utils.get_peer_id(entity)),
                "title": getattr(entity, "title", None) or ref,
                "username": core.primary_username(entity),
                "is_channel": isinstance(entity, Channel),
            })
            log.info("✅ Група: %s", self.groups[-1]["title"])
        if not self.groups:
            sys.exit("❌ Жодну групу не знайдено. Перевірте GROUPS у .env (для приватних груп ваш акаунт має бути учасником).")

    async def init_positions(self) -> None:
        """Перший запуск: запам'ятовуємо останнє повідомлення, щоб не слати старі."""
        for g in self.groups:
            if g["key"] in self.state["last_ids"]:
                continue
            if INITIAL_LOOKBACK:
                msgs = await self.client.get_messages(g["entity"], limit=INITIAL_LOOKBACK)
                start = (min(m.id for m in msgs) - 1) if msgs else 0
            else:
                msgs = await self.client.get_messages(g["entity"], limit=1)
                start = msgs[0].id if msgs else 0
            self.state["last_ids"][g["key"]] = start
        self.save_state()

    async def announce_start(self) -> None:
        names = "\n".join(f"• {g['title']}" for g in self.groups)
        text = (f"✅ <b>Моніторинг запущено</b>\nПеревіряю кожні {CHECK_INTERVAL} с:\n"
                f"{html.escape(names)}\n\nКоманди: /status /check /pause /resume")
        ok, res = await self.bot.send(self.notify_chat, text)
        if ok:
            return
        # Бот ще не може писати вам, поки ви не натиснули Start — натиснемо за вас.
        if not NOTIFY_CHAT_ID and self.bot_username:
            log.info("Відкриваю чат із @%s від вашого імені (/start)…", self.bot_username)
            try:
                await self.client.send_message(self.bot_username, "/start")
                await asyncio.sleep(2)
                ok, res = await self.bot.send(self.notify_chat, text)
            except Exception as e:
                log.warning("Не вдалося надіслати /start боту: %s", e)
        if not ok:
            log.error("⚠️ Бот не може надіслати повідомлення в чат %s: %s\n"
                      "   Відкрийте @%s у Telegram і натисніть «Start» (або перевірте NOTIFY_CHAT_ID).",
                      self.notify_chat, res.get("description"), self.bot_username)

    # ---------- перевірка ----------
    async def check_group(self, g: dict) -> None:
        last = self.state["last_ids"].get(g["key"], 0)
        newest = last
        async for m in self.client.iter_messages(g["entity"], min_id=last, reverse=True, limit=500):
            newest = max(newest, m.id)
            if m.action:                      # службові: «хтось увійшов» тощо
                continue
            text = m.message or ""
            matched = core.find_matches(text, self.keywords, self.excludes)
            if not matched:
                continue
            # те саме оголошення (назва станції + ціна) протягом DEDUP_MINUTES у будь-якому каналі — пропускаємо
            ident = core.announcement_key(text)
            posted = m.date.timestamp()
            prev = self.state["recent"].get(ident)
            if DEDUP_MINUTES and prev is not None and abs(posted - prev) <= DEDUP_MINUTES * 60:
                log.info("↺ Те саме оголошення вже було %s хв тому — пропускаю (%s)", round(abs(posted - prev) / 60), ident)
                continue
            self.state["recent"][ident] = posted
            await self.notify(g, m, text, matched)
        self.state["last_ids"][g["key"]] = newest

    async def notify(self, g: dict, m, text: str, matched: list[str]) -> None:
        link = core.message_link(m.id, g["username"], g["entity"].id, g["is_channel"])
        sender = m.sender
        if sender is None:
            try:
                sender = await m.get_sender()
            except Exception:
                sender = None
        if isinstance(sender, User):
            author = core.format_author(sender.first_name, sender.last_name,
                                        core.primary_username(sender), sender.id, True)
        elif sender is not None:
            author = core.format_author(getattr(sender, "title", None), None,
                                        core.primary_username(sender), None, False)
        else:
            author = None
        when = (m.date.astimezone(TZ) if TZ else m.date.astimezone()).strftime("%d.%m.%Y %H:%M")
        html_text = core.format_notification(group_title=g["title"], text=text, link=link,
                                             matched=matched, author_html=author, when=when)
        self.state["found_total"] += 1
        self.found_session += 1
        log.info("🔋 Знайдено в «%s»: %s (%s)", g["title"], link or f"msg {m.id}", ", ".join(matched[:3]))
        ok, _ = await self.bot.send(self.notify_chat, html_text, link)
        if not ok:
            self.pending.append((html_text, link))
            del self.pending[:-200]

    async def flush_pending(self) -> None:
        if not self.pending:
            return
        queue, self.pending = self.pending, []
        for html_text, link in queue:
            ok, _ = await self.bot.send(self.notify_chat, html_text, link)
            if not ok:
                self.pending.append((html_text, link))

    async def poll_forever(self) -> None:
        while True:
            started = time.monotonic()
            if not self.paused:
                await self.flush_pending()
                for g in self.groups:
                    try:
                        await self.check_group(g)
                    except FloodWaitError as e:
                        log.warning("Telegram просить зачекати %s с", e.seconds)
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        log.exception("Помилка під час перевірки «%s»", g["title"])
                self.last_check = now_local()
                self.save_state()
            delay = max(1.0, CHECK_INTERVAL - (time.monotonic() - started))
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    # ---------- команди бота ----------
    def status_text(self) -> str:
        groups = []
        for g in self.groups:
            url = f"https://t.me/{g['username']}" if g["username"] else None
            title = html.escape(g["title"])
            groups.append(f'• <a href="{url}">{title}</a>' if url else f"• {title}")
        last = self.last_check.strftime("%H:%M:%S") if self.last_check else "ще не було"
        return (f"{'⏸ <b>На паузі</b>' if self.paused else '▶️ <b>Працює</b>'}\n"
                f"Інтервал: {CHECK_INTERVAL} с · остання перевірка: {last}\n"
                f"Ключових слів: {len(self.keywords)} · виключень: {len(self.excludes)}\n"
                f"Знайдено з моменту запуску: {self.found_session} · усього: {self.state['found_total']}\n\n"
                "Групи:\n" + "\n".join(groups))

    async def handle_command(self, chat_id, text: str) -> None:
        cmd = text.split()[0].split("@")[0].lower()
        owner = str(chat_id) == str(self.notify_chat)
        if not owner:
            if cmd in ("/start", "/id"):
                await self.bot.send(chat_id, f"Це приватний бот. Ваш chat id: <code>{chat_id}</code>")
            return
        if cmd in ("/start", "/help"):
            reply = ("Я стежу за оголошеннями про зарядні станції і надсилаю їх сюди з посиланням.\n\n"
                     "/status — стан і список груп\n/check — перевірити зараз\n"
                     "/pause — призупинити\n/resume — продовжити (пропущене за паузу надійде)\n"
                     f"/id — chat id (<code>{chat_id}</code>)")
        elif cmd == "/status":
            reply = self.status_text()
        elif cmd == "/check":
            self.wake.set()
            reply = "🔎 Перевіряю зараз…"
        elif cmd == "/pause":
            self.paused = True
            reply = "⏸ Пауза. /resume — продовжити."
        elif cmd == "/resume":
            self.paused = False
            self.wake.set()
            reply = "▶️ Продовжую."
        elif cmd == "/id":
            reply = f"chat id: <code>{chat_id}</code>"
        else:
            return
        await self.bot.send(chat_id, reply)

    async def command_loop(self) -> None:
        offset = None
        while True:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            try:
                res = await self.bot.call("getUpdates", params, http_timeout=45)
            except Exception as e:
                log.debug("getUpdates: %s", e)
                await asyncio.sleep(5)
                continue
            if not res.get("ok"):
                if res.get("error_code") == 409:
                    log.warning("Схоже, бот запущено двічі (або в нього є webhook). Команди тимчасово не працюють.")
                await asyncio.sleep(30)
                continue
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = msg.get("text") or ""
                if text.startswith("/"):
                    try:
                        await self.handle_command(msg["chat"]["id"], text)
                    except Exception:
                        log.exception("Помилка команди %r", text)

    # ---------- головне ----------
    async def run(self) -> None:
        self.check_config()
        self.wake = asyncio.Event()
        me_bot: dict = {}
        for attempt in range(5):
            try:
                me_bot = await self.bot.call("getMe")
                break
            except Exception as e:
                log.warning("Немає зв'язку з api.telegram.org (%s), повтор через 10 с…", e)
                await asyncio.sleep(10)
        else:
            sys.exit("❌ Не вдається підключитися до api.telegram.org. Перевірте інтернет / VPN / проксі.")
        if not me_bot.get("ok"):
            sys.exit(f"❌ Невірний BOT_TOKEN: {me_bot.get('description')}")
        self.bot_username = me_bot["result"]["username"]
        log.info("Бот для сповіщень: @%s", self.bot_username)

        self.client = TelegramClient(SESSION_FILE, API_ID, API_HASH,
                                     connection_retries=None, retry_delay=5,
                                     device_model="Charger ads monitor")
        print("Вхід у ваш акаунт Telegram (лише перший раз попросить номер і код)…")
        await self.client.start(
            phone=lambda: input("Номер телефону вашого акаунта (+380…): ").strip(),
            code_callback=lambda: input("Код, який прийшов у Telegram: ").strip(),
            password=lambda: getpass.getpass("Пароль двоетапної перевірки (якщо є): "),
        )
        me = await self.client.get_me()
        log.info("Читаю групи від імені: %s", core.primary_username(me) or me.first_name)
        if not self.notify_chat:
            self.notify_chat = me.id

        await self.resolve_groups()
        await self.init_positions()
        await self.announce_start()
        log.info("Перевірка кожні %s с. Ключових слів: %s. Ctrl+C — зупинити.", CHECK_INTERVAL, len(self.keywords))

        commands = asyncio.create_task(self.command_loop())
        try:
            await self.poll_forever()
        finally:
            commands.cancel()
            self.save_state()
            await self.client.disconnect()


def main() -> None:
    setup_logging()
    try:
        asyncio.run(Monitor().run())
    except KeyboardInterrupt:
        log.info("Зупинено.")


if __name__ == "__main__":
    main()
