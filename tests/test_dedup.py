"""Дублікати й вигляд сповіщення. Запуск: python tests/test_dedup.py

Однакове оголошення = та сама назва станції + та сама ціна протягом 30 хв, у будь-якому каналі.
Тексти — справжні пости з каналів (скорочено)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(BOT_TOKEN="1:test", GROUPS="@ZBSkitai,@technorika,@egogorushka88",
                  TG_SESSION="", NOTIFY_CHAT_ID="777")

import core  # noqa: E402
import github_run  # noqa: E402
import web_source  # noqa: E402

# ---------- 1. Ключ «того самого оголошення» ----------
same = [
    ("ЦЕНА ШАРА!\nOUKITEL P800E - €163⚡️\n800W / 512Wh",
     "Портативная электростанция OUKITEL P800E 800 Вт, 512 Вт·ч, LiFePO4\n💶 Цена от: 163.40€"),
    ("OSCAL Max 2400SE ~ €588⚡️\n2400W / 2016Wh", "OSCAL PowerMax2400SE ⚡️ — від €588.00 (DE)!"),
    ("IEETek P3200 - €575🔥", "Портативная электростанция IEE P3200 мощностью 3200 Вт\n💶 Цена от: 575€"),
    ("Отлично!\nІЕЕTek P1800 - €320 ⚡️", "IEETek P1800 - €320"),
    ("Jackery 1000 V2 - 375€⚡️", "Jackery Explorer 1000 V2 — €375"),
    ("Станция Gaiaray - €105⚡️\n500W / 460Wh", "Станция Gaiaray - €105⚡️\n500W / 460Wh"),
]
different = [
    ("OUKITEL P800E, 800 Вт\n💶 Цена от: 184.03€", "Портативная электростанция OUKITEL P800E 800 Вт\n💶 Цена от: 179.53€"),  # інша ціна
    ("OUKITEL P1000E Plus - €331⚡️", "OUKITEL P1000 Plus 1800W ⚡️ — від €331.22"),          # інша модель
    ("OUKITEL P800E - €177", "• Oukitel P1500 386€\n• Oukitel P1000 337€\n• Oukitel P800 179€"),  # добірка
    ("Necespow N1200 - €281", "Necespow N1800 - €281"),
]
for a, b in same:
    assert core.announcement_key(a) == core.announcement_key(b), (a, b, core.announcement_key(a), core.announcement_key(b))
for a, b in different:
    assert core.announcement_key(a) != core.announcement_key(b), (a, b, core.announcement_key(a))
print(f"keys OK: {len(same)} пар однакові, {len(different)} пар різні")

# ---------- 2. Перший рядок — назва, другий — ціна ----------
msg = core.format_notification(group_title="ZBS Kitai", text="ЦЕНА ШАРА!\nOUKITEL P800E - €163⚡️\n800W / 512Wh",
                               link="https://t.me/ZBSkitai/2440", when="24.10.2026 12:44")
first, second = msg.splitlines()[:2]
assert first == "🔋 <b>OUKITEL P800E</b>" and second == "💶 <b>€163</b>", (first, second)
assert "🔑" not in msg and "зарядн*" not in msg
print("format OK:", first, "/", second)

# ---------- 3. Повний прогін бота: різні канали, різний час ----------
T0 = "2026-10-25T09:00:00+00:00"


def at(minutes):
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(T0) + timedelta(minutes=minutes)).isoformat()


feed = {"ZBSkitai": [], "technorika": [], "egogorushka88": []}


def fake_fetch_new(channel, last_id, max_pages=5):
    return f"Канал {channel}", [p for p in feed[channel] if p.id > last_id]


sent = []


class FakeBot:
    def __init__(self, token):
        pass

    async def call(self, method, params=None, http_timeout=30):
        return {"ok": True, "result": {"username": "EstationC_bot"} if method == "getMe" else []}

    async def send(self, chat_id, text, button_url=None):
        sent.append(button_url)
        return True, {"ok": True}

    async def send_photo(self, *a, **kw):
        return False, {"description": "no photo in test"}


web_source.fetch_new = fake_fetch_new
github_run.BotAPI = FakeBot
tmp = Path(tempfile.mkdtemp())
github_run.STATE_DIR = tmp
github_run.STATE_FILE = tmp / "state.json"


def run():
    r = github_run.Run()
    r.state["announced"] = True
    asyncio.run(r.main())
    return r


P = web_source.WebPost
for ch in feed:                                   # стартові позиції
    feed[ch].append(P(1, "старий пост", at(-60)))
run()

feed["ZBSkitai"].append(P(2, "OUKITEL P800E - €163⚡️\n800W / 512Wh", at(0)))
feed["egogorushka88"].append(P(2, "Портативная электростанция OUKITEL P800E 800 Вт\n💶 Цена от: 163.40€", at(10)))  # дубль з іншого каналу
feed["ZBSkitai"].append(P(3, "ЦЕНА!\nOUKITEL P800E - €163⚡️", at(20)))           # дубль у тому самому каналі
feed["technorika"].append(P(2, "OUKITEL P800E — від €158 (Польща)!", at(21)))   # та сама станція, інша ціна
r = run()
assert sent == ["https://t.me/ZBSkitai/2", "https://t.me/technorika/2"], sent
assert r.duplicates == 2, r.duplicates
print("run OK: з 4 постів надіслано 2, пропущено 2 дублі (інший канал і той самий канал)")

sent.clear()
feed["ZBSkitai"].append(P(4, "OUKITEL P800E - €163⚡️", at(45)))                 # через 45 хв — знову надсилаємо
r = run()
assert sent == ["https://t.me/ZBSkitai/4"], sent
print("window OK: те саме оголошення через 45 хв надіслано знову")
