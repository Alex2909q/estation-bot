#!/usr/bin/env python3
"""Сторож вільних талонів на практичний іспит — ТСЦ МВС № 1843 (Коростень, вул. С. Кемського, 3).

Як працює
---------
Сайт е-запису (eqn.hsc.gov.ua) пускає лише після входу через ID.GOV.UA і захищений від ботів,
тому напряму його не опитати. Натомість офіційний ТалонБот ГСЦ МВС сам перевіряє вільні талони
в обраному сервісному центрі й пише про них у Telegram. Скрипт працює від вашого акаунта:
кожні 15 хвилин дивиться в чат з ТалонБотом і, якщо з'явилося нове сповіщення про вільний талон,
пересилає його контакту «Голубоглазка Оля».

Налаштування (Secrets / змінні середовища)
------------------------------------------
  EXAM_SESSION     "api_id:api_hash:сесія" — у Monitor це та сама TG_SESSION (крок іде після перевірки каналів, не одночасно).
                   Не використовуйте ту саму TG_SESSION, що й Monitor: одна сесія одночасно з двох
                   серверів Telegram відкликає (AUTH_KEY_DUPLICATED).
  EXAM_RECIPIENT   кому слати; ім'я контакту, @username або телефон. Типово «Голубоглазка Оля».
  EXAM_TALON_BOT   @username ТалонБота; якщо порожньо — шукаю серед ваших чатів бота з «талон» у назві.
  EXAM_MATCH       слова, з яких хоч одне має бути в сповіщенні (через кому). Типово: коростен,1843,вільн,талон
  EXAM_INTERVAL_MIN  інтервал перевірки, хв (типово 15)
  EXAM_RUN_MINUTES   скільки хвилин живе один запуск (типово 340; далі GitHub ставить наступний)
  EXAM_REPEAT_MIN    не повторювати однаковий текст частіше, ніж раз на N хв (типово 60)
  EXAM_DRY_RUN=1     тестовий режим: усе шлеться у ваше «Збережене», а не Олі
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("exam")

CENTER = "ТСЦ МВС № 1843, м. Коростень, вул. С. Кемського, 3"
BOOKING_URL = "https://eqn.hsc.gov.ua/"
MARK = "ТалонБот · №"                       # мітка в нашому повідомленні: з неї відновлюємо, що вже надіслано
NOT_ALERT = re.compile(r"обер(іть|ли)|вибер(іть|ли)|підписа|скасова|відписа|меню|оберіть послугу", re.I)
HEALTH_FILE = Path("/tmp/exam_healthy")


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def creds():
    raw = env("EXAM_SESSION") or env("TG_SESSION")
    parts = raw.split(":", 2)
    if len(parts) == 3 and parts[0].isdigit():
        return int(parts[0]), parts[1], parts[2]
    if raw and env("API_ID").isdigit() and env("API_HASH"):
        return int(env("API_ID")), env("API_HASH"), raw
    raise SystemExit("Немає секрету EXAM_SESSION (формат api_id:api_hash:сесія) — див. exam/README.md")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("ʼ", "'").replace("’", "'")).strip().casefold()


class Watcher:
    def __init__(self, client):
        self.c = client
        self.dry = env("EXAM_DRY_RUN") in ("1", "true", "yes")
        self.match = [norm(w) for w in env("EXAM_MATCH", "коростен,1843,вільн,талон").split(",") if w.strip()]
        self.repeat = timedelta(minutes=int(env("EXAM_REPEAT_MIN", "60") or 60))
        self.bot = None
        self.to = None
        self.last_id = 0                    # останнє оброблене повідомлення ТалонБота
        self.last_text = ""
        self.last_sent_at = datetime.min.replace(tzinfo=timezone.utc)

    # ---------------------------------------------------------- пошук чатів ---
    async def find_bot(self):
        ref = env("EXAM_TALON_BOT")
        if ref:
            return await self.c.get_entity(ref)
        async for d in self.c.iter_dialogs():
            if getattr(d.entity, "bot", False) and "талон" in norm(d.name):
                return d.entity
        raise SystemExit("Не знайшов ТалонБот серед ваших чатів. Відкрийте його в Telegram, натисніть «Почати», "
                         "налаштуйте відстеження ТСЦ 1843 — або вкажіть EXAM_TALON_BOT.")

    async def find_recipient(self):
        if self.dry:
            return await self.c.get_me()
        ref = env("EXAM_RECIPIENT", "Голубоглазка Оля")
        if ref.startswith("@") or ref.startswith("+") or ref.isdigit():
            return await self.c.get_entity(ref)
        from telethon.tl.functions.contacts import GetContactsRequest
        want = norm(ref)
        res = await self.c(GetContactsRequest(hash=0))
        for u in res.users:
            first, last = u.first_name or "", u.last_name or ""
            if want in (norm(f"{first} {last}"), norm(f"{last} {first}")):
                return u
        async for d in self.c.iter_dialogs():            # запасний варіант — за назвою чату
            if norm(d.name) == want:
                return d.entity
        raise SystemExit(f"Не знайшов контакт «{ref}» у вашому Telegram. Перевірте ім'я або вкажіть EXAM_RECIPIENT.")

    async def restore(self):
        """Що вже надсилали — дивимось у власних повідомленнях одержувачу (мітка «ТалонБот · №…»)."""
        async for m in self.c.iter_messages(self.to, from_user="me", search=MARK, limit=20):
            found = re.search(re.escape(MARK) + r"(\d+)", m.raw_text or "")
            if found and int(found.group(1)) > self.last_id:
                self.last_id = int(found.group(1))
                self.last_sent_at = m.date
                raw = m.raw_text or ""
                self.last_text = norm(raw.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]) if raw.count("\n\n") >= 2 else ""
        if not self.last_id:                              # перший запуск: старі сповіщення не чіпаємо
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
            async for m in self.c.iter_messages(self.bot, limit=50):
                if m.date < cutoff:
                    self.last_id = max(self.last_id, m.id)
                    break
        log.info("Відновлено: останнє оброблене повідомлення ТалонБота №%s", self.last_id)

    # -------------------------------------------------------------- перевірка ---
    def is_alert(self, text: str) -> bool:
        t = norm(text)
        return bool(t) and any(w in t for w in self.match) and not NOT_ALERT.search(t)

    async def check(self) -> None:
        new = [m async for m in self.c.iter_messages(self.bot, min_id=self.last_id, limit=50)]
        new.sort(key=lambda m: m.id)
        if not new:
            log.info("Нових повідомлень від ТалонБота немає")
            return
        last_out = max((m.date for m in new if m.out), default=None)
        alerts = []
        for m in new:
            if m.out:
                continue
            if last_out and timedelta(0) <= m.date - last_out < timedelta(minutes=3):
                continue                                 # відповідь бота на ваші натискання в меню
            if self.is_alert(m.raw_text):
                alerts.append(m)
        self.last_id = max(m.id for m in new)
        if not alerts:
            log.info("Нові повідомлення є, але це не сповіщення про талон")
            return
        m = alerts[-1]                                   # ТалонБот повторює те саме кожні 10 хв — шлемо останнє
        body = (m.raw_text or "").strip()
        now = datetime.now(timezone.utc)
        if norm(body) == self.last_text and now - self.last_sent_at < self.repeat:
            log.info("Те саме сповіщення вже надсилали %s — пропускаю", self.last_sent_at)
            return
        text = (f"🚗 З'явилося місце на практичний іспит!\n{CENTER}\n\n{body}\n\n"
                f"Записатися: {BOOKING_URL} (вхід через ID.GOV.UA). Місця розбирають за лічені хвилини.\n"
                f"{MARK}{m.id}")
        await self.c.send_message(self.to, text, link_preview=False)
        self.last_text, self.last_sent_at = norm(body), now
        log.info("Надіслано сповіщення (повідомлення ТалонБота №%s)%s", m.id, " [тест]" if self.dry else "")


STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "exam.json"


async def once() -> int:
    """Одна перевірка — для кроку у workflow Monitor (той уже працює кожні ~2 хв із TG_SESSION).
    Сама перевірка — не частіше, ніж раз на EXAM_INTERVAL_MIN. Помилки не валять Monitor."""
    import json

    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import InputPeerUser

    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        st = {}
    interval = int(env("EXAM_INTERVAL_MIN", "15") or 15) * 60
    if time.time() - st.get("last_check", 0) < interval - 30:
        log.info("Exam 1843: наступна перевірка через %s с", int(interval - (time.time() - st.get("last_check", 0))))
        return 0

    api_id, api_hash, session = creds()
    client = TelegramClient(StringSession(session), api_id, api_hash, device_model="EStation GitHub")
    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.warning("Exam 1843: сесія недійсна")
            return 0
        w = Watcher(client)
        key = "test" if w.dry else "live"
        peers = st.setdefault("peers", {})
        try:
            p = peers.get("bot")
            w.bot = InputPeerUser(p[0], p[1]) if p else await w.find_bot()
            p = peers.get(key)
            w.to = InputPeerUser(p[0], p[1]) if p else await w.find_recipient()
        except SystemExit as e:                          # ТалонБот ще не налаштований / контакт не знайдено
            log.warning("Exam 1843: %s", e)
            st["last_check"] = time.time()
            st["problem"] = str(e)
            return 0
        for name, ent in (("bot", w.bot), (key, w.to)):
            if name not in peers:
                ip = await client.get_input_entity(ent)
                peers[name] = [ip.user_id, ip.access_hash]
        await w.restore()
        await w.check()
        st["last_check"] = time.time()
        st.pop("problem", None)
        HEALTH_FILE.write_text("ok")
    except Exception as e:
        log.warning("Exam 1843: перевірка не вдалася: %s", e)
    finally:
        await client.disconnect()
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        (STATE_FILE.parent / "changed").write_text("1")
    return 0


async def main() -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash, session = creds()
    interval = int(env("EXAM_INTERVAL_MIN", "15") or 15) * 60
    deadline = time.monotonic() + int(env("EXAM_RUN_MINUTES", "340") or 340) * 60

    client = TelegramClient(StringSession(session), api_id, api_hash, device_model="Exam watcher GitHub")
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("EXAM_SESSION недійсна або відкликана — створіть нову через make_session.command")
    try:
        w = Watcher(client)
        w.bot = await w.find_bot()
        w.to = await w.find_recipient()
        await w.restore()
        HEALTH_FILE.write_text("ok")
        log.info("Стежу: ТалонБот → %s, кожні %s хв", "Збережене (тест)" if w.dry else env("EXAM_RECIPIENT", "Голубоглазка Оля"),
                 interval // 60)
        while True:
            try:
                await w.check()
            except Exception as e:                       # тимчасові збої мережі не зупиняють сторожа
                log.warning("Перевірка не вдалася: %s", e)
            if time.monotonic() + interval > deadline:
                break
            await asyncio.sleep(interval)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    if "--once" in sys.argv:
        sys.exit(asyncio.run(once()))
    asyncio.run(main())
