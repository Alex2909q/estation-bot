#!/usr/bin/env python3
"""Одна перевірка каналів — для запуску за розкладом у GitHub Actions.

Налаштування — через Secrets репозиторію:
  BOT_TOKEN       (обов'язково) токен бота від @BotFather
  NOTIFY_CHAT_ID  (необов'язково) куди слати; інакше — тому, хто першим натисне Start у боті
  TG_SESSION, API_ID, API_HASH  (необов'язково) читати канали через ваш акаунт —
                  потрібно для каналів без веб-версії (@dobrymao_UA, @dealradarua). Див. make_session.command

Без TG_SESSION канали читаються з публічної веб-версії t.me/s/<канал> — акаунт не потрібен.
Стан (на чому зупинились, хто власник) лежить у state/state.json і зберігається кешем Actions.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import core
import web_source
from botapi import BotAPI

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "state"
STATE_FILE = STATE_DIR / "state.json"
KEYWORDS_FILE = BASE / "keywords.txt"
EXCLUDE_FILE = BASE / "exclude.txt"
CHANNELS_FILE = BASE / "channels.txt"
WORKFLOW_FILE = "monitor.yml"

log = logging.getLogger("monitor")


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(env("TIMEZONE", "Europe/Kyiv"))
    except Exception:
        return None


TZ = tz()


def load_channels() -> list[str]:
    raw = env("GROUPS")
    if raw:
        items = raw.replace("\n", ",").split(",")
    elif CHANNELS_FILE.exists():
        items = [l.split("#")[0] for l in CHANNELS_FILE.read_text(encoding="utf-8").splitlines()]
    else:
        items = []
    return [i.strip() for i in items if i.strip()]


def load_state() -> dict:
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        st = {}
    for k, v in {"last_ids": {}, "seen": {}, "pending": [], "titles": {}, "found_total": 0,
                 "owner_chat_id": None, "update_offset": None, "announced": False,
                 "warned": {}, "keepalive": "", "peers": {}}.items():
        st.setdefault(k, v)
    return st


def save_state(st: dict, before_hash: str) -> None:
    dedup_h = int(env("DEDUP_HOURS", "24") or 24)
    cutoff = time.time() - max(dedup_h, 1) * 3600
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= cutoff}
    st["pending"] = st["pending"][-30:]
    data = json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True)
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(data, encoding="utf-8")
    changed = hashlib.sha1(data.encode()).hexdigest() != before_hash
    marker = STATE_DIR / "changed"
    if changed:
        marker.write_text("1")       # сигнал workflow: зберегти новий кеш
    elif marker.exists():
        marker.unlink()


def state_hash(st: dict) -> str:
    return hashlib.sha1(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True).encode()).hexdigest()


def fmt_time(iso_or_dt) -> str:
    try:
        dt = iso_or_dt if isinstance(iso_or_dt, datetime) else datetime.fromisoformat(iso_or_dt)
        return (dt.astimezone(TZ) if TZ else dt).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


# ------------------------------------------------------------ джерела ---

def account_creds():
    """TG_SESSION = "api_id:api_hash:рядок_сесії" (так його створює make_session.py)
    або лише рядок сесії + окремі секрети API_ID і API_HASH."""
    raw = env("TG_SESSION")
    if not raw:
        return None
    parts = raw.split(":", 2)
    if len(parts) == 3 and parts[0].isdigit():
        return int(parts[0]), parts[1], parts[2]
    if env("API_ID").isdigit() and env("API_HASH"):
        return int(env("API_ID")), env("API_HASH"), raw
    return None


class AccountReader:
    """Читання через акаунт користувача (Telethon + рядок сесії)."""

    def __init__(self, creds, peers: dict):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        api_id, api_hash, session = creds
        self.client = TelegramClient(StringSession(session), api_id, api_hash,
                                     device_model="EStation GitHub")
        self.peers = peers          # кеш id/access_hash між запусками — щоб не ловити ліміти Telegram

    async def __aenter__(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise RuntimeError("TG_SESSION недійсна або відкликана — створіть нову через make_session")
        return self

    async def __aexit__(self, *exc):
        await self.client.disconnect()

    async def _entity(self, ref: str):
        from telethon.tl.types import InputPeerChannel
        key = str(core.parse_group_ref(ref))
        cached = self.peers.get(key)
        if cached:
            return InputPeerChannel(cached[0], cached[1]), cached[2]
        entity = await self.client.get_entity(core.parse_group_ref(ref))
        username = core.primary_username(entity)
        if getattr(entity, "access_hash", None) is not None:
            self.peers[key] = [entity.id, entity.access_hash, username]
        return entity, username

    async def fetch_new(self, ref: str, last_id: int):
        entity, username = await self._entity(ref)
        title = None
        if not isinstance(entity, (str, int)) and hasattr(entity, "title"):
            title = entity.title
        posts = []
        if last_id:
            async for m in self.client.iter_messages(entity, min_id=last_id, reverse=True, limit=300):
                if not m.action:
                    posts.append(web_source.WebPost(m.id, m.message or "", m.date.isoformat()))
        else:
            latest = await self.client.get_messages(entity, limit=1)
            posts = [web_source.WebPost(m.id, m.message or "", m.date.isoformat()) for m in latest]
        return title, username, entity, posts


# ------------------------------------------------------------ основне ---

class Run:
    def __init__(self):
        self.bot = BotAPI(env("BOT_TOKEN"))
        self.state = load_state()
        self.keywords = core.load_keywords(KEYWORDS_FILE)
        self.excludes = core.load_keywords(EXCLUDE_FILE)
        self.channels = load_channels()
        self.creds = account_creds()
        self.account = self.creds is not None
        raw = env("TG_SESSION")
        if raw and not self.account:                 # вміст секрету не показуємо — лише форму
            log.warning("TG_SESSION має неочікуваний формат (довжина %s, двокрапок %s, "
                        "перша частина — %s). Очікується api_id:api_hash:сесія від make_session.command.",
                        len(raw), raw.count(":"),
                        "число" if raw.split(":")[0].isdigit() else "не число")
            self.problems["TG_SESSION"] = "неправильний формат — створіть заново через make_session.command"
        self.problems: dict[str, str] = {}

    @property
    def owner(self):
        return env("NOTIFY_CHAT_ID") or self.state["owner_chat_id"]

    # ----- команди бота (/start, /status) -----
    async def process_updates(self) -> None:
        params = {"timeout": 0, "allowed_updates": ["message"]}
        if self.state["update_offset"]:
            params["offset"] = self.state["update_offset"]
        res = await self.bot.call("getUpdates", params)
        if not res.get("ok"):
            log.warning("getUpdates: %s", res.get("description"))
            return
        for upd in res.get("result", []):
            self.state["update_offset"] = upd["update_id"] + 1
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat_id = (msg.get("chat") or {}).get("id")
            if not text.startswith("/") or chat_id is None:
                continue
            cmd = text.split()[0].split("@")[0].lower()
            if not self.owner and cmd == "/start":
                self.state["owner_chat_id"] = chat_id
                log.info("Власника бота визначено (натиснув Start).")
            if str(chat_id) != str(self.owner):
                await self.bot.send(chat_id, "Це приватний бот.")
                continue
            if cmd in ("/start", "/help"):
                await self.bot.send(chat_id, "✅ Підключено. Нові оголошення про зарядні станції "
                                             "надходитимуть сюди.\n/status — стан моніторингу")
            elif cmd == "/status":
                await self.bot.send(chat_id, self.status_text())

    def status_text(self) -> str:
        lines = []
        for ref in self.channels:
            name = str(core.parse_group_ref(ref))
            title = self.state["titles"].get(name, name)
            mark = "⚠️ " + self.problems[name] if name in self.problems else "✅"
            lines.append(f"• {html.escape(title)} — {html.escape(mark)}")
        mode = "через ваш акаунт" if self.account else "через веб-версію каналів"
        return (f"▶️ <b>Працює на GitHub</b> ({mode})\n"
                f"Перевірка ~кожні 5 хв · остання: {fmt_time(datetime.now().astimezone())}\n"
                f"Знайдено всього: {self.state['found_total']}\n\n" + "\n".join(lines))

    # ----- перевірка каналів -----
    async def check_all(self) -> None:
        if self.account:
            try:
                async with AccountReader(self.creds, self.state["peers"]) as reader:
                    for ref in self.channels:
                        await self.check_one(ref, reader)
                return
            except Exception as e:                  # сесія зламалась — працюємо хоча б через веб
                log.warning("Акаунт недоступний (%s) — читаю через веб-версію.", e)
                self.problems["TG_SESSION"] = f"не працює: {e}"
                self.account = False
        for ref in self.channels:
            await self.check_one(ref, None)

    async def check_one(self, ref: str, reader) -> None:
        target = core.parse_group_ref(ref)
        key = str(target)
        last = int(self.state["last_ids"].get(key, 0))
        try:
            if reader is not None:
                title, username, _, posts = await reader.fetch_new(ref, last)
            else:
                if not isinstance(target, str) or target.startswith("http"):
                    raise ValueError("без TG_SESSION підтримуються лише публічні канали (@назва)")
                title, posts = await asyncio.to_thread(web_source.fetch_new, target, last)
                username = target
        except web_source.NoWebPreview:
            self.problems[key] = "немає веб-версії, потрібен TG_SESSION"
            log.warning("@%s: у каналу вимкнена веб-версія — його можна читати лише з TG_SESSION.", key)
            return
        except Exception as e:
            self.problems[key] = "помилка читання"
            log.warning("%s: не вдалося прочитати (%s)", ref, e)
            return

        if title:
            self.state["titles"][key] = title
        title = title or key
        if not posts:
            log.info("%s: нових постів немає", title)
            return
        newest = max(p.id for p in posts)
        if not last:                                # перший запуск — лише запам'ятати позицію
            lookback = int(env("INITIAL_LOOKBACK", "0") or 0)
            if not lookback:
                self.state["last_ids"][key] = newest
                log.info("%s: старт з поста %s", title, newest)
                return
            posts = posts[-lookback:]

        found = 0
        for p in posts:
            if p.id <= last:
                continue
            matched = core.find_matches(p.text, self.keywords, self.excludes)
            if not matched:
                continue
            fp = core.text_fingerprint(p.text)
            if int(env("DEDUP_HOURS", "24") or 24) and fp in self.state["seen"]:
                continue
            self.state["seen"][fp] = time.time()
            link = core.message_link(p.id, username, 0, True) if username else None
            text = core.format_notification(group_title=title, text=p.text, link=link, matched=matched,
                                            author_html=html.escape(p.author) if p.author else None,
                                            when=fmt_time(p.date) if p.date else "")
            self.state["pending"].append([text, link])
            self.state["found_total"] += 1
            found += 1
            log.info("🔋 %s: %s", title, link or p.id)
        self.state["last_ids"][key] = max(newest, last)
        log.info("%s: нових постів %s, оголошень %s", title, len(posts), found)

    async def flush(self) -> None:
        if not self.owner:
            if self.state["pending"]:
                log.warning("Є %s сповіщень, але ніхто ще не натиснув Start у боті.", len(self.state["pending"]))
            return
        if not self.state["announced"]:
            names = "\n".join(f"• {html.escape(self.state['titles'].get(str(core.parse_group_ref(c)), c))}"
                              for c in self.channels)
            ok, _ = await self.bot.send(self.owner, "✅ <b>Моніторинг на GitHub запущено</b>\n"
                                                    f"Перевіряю приблизно кожні 5 хвилин:\n{names}")
            self.state["announced"] = ok
        for key, problem in self.problems.items():
            today = datetime.now().strftime("%Y-%m-%d")
            if self.state["warned"].get(key) != today:           # не частіше разу на день
                await self.bot.send(self.owner, f"⚠️ {html.escape(key)}: {html.escape(problem)}")
                self.state["warned"][key] = today
        queue, self.state["pending"] = self.state["pending"], []
        for text, link in queue:
            ok, res = await self.bot.send(self.owner, text, link)
            if not ok:
                log.warning("Не вдалося надіслати: %s", res.get("description"))
                self.state["pending"].append([text, link])

    def keepalive(self) -> None:
        """GitHub вимикає розклад у публічних репозиторіях після 60 днів без активності.
        Раз на день «вмикаємо» workflow ще раз, щоб цього не сталося."""
        token, repo = env("GITHUB_TOKEN"), env("GITHUB_REPOSITORY")
        today = datetime.now().strftime("%Y-%m-%d")
        if not token or not repo or self.state["keepalive"] == today:
            return
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/enable",
            method="PUT", headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"})
        try:
            urllib.request.urlopen(req, timeout=20).close()
            self.state["keepalive"] = today
        except Exception as e:
            log.info("keepalive: %s", e)

    async def main(self) -> int:
        if not env("BOT_TOKEN"):
            log.error("Не задано секрет BOT_TOKEN (Settings → Secrets and variables → Actions).")
            return 1
        if not self.channels:
            log.error("Список каналів порожній (channels.txt).")
            return 1
        me = await self.bot.call("getMe")
        if not me.get("ok"):
            log.error("BOT_TOKEN недійсний: %s", me.get("description"))
            return 1
        before = state_hash(self.state)
        await self.process_updates()
        await self.check_all()
        await self.flush()
        self.keepalive()
        save_state(self.state, before)
        if not self.owner:
            log.warning("Відкрийте @%s у Telegram і натисніть Start — тоді почнуть надходити сповіщення.",
                        me["result"]["username"])
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    sys.exit(asyncio.run(Run().main()))
