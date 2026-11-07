"""«Жваві обговорення»: пост (не станція), під яким коментарів більше за 4, надсилається один раз.
Запуск: python tests/test_hot.py"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(BOT_TOKEN="1:test", GROUPS="@ZBSkitai,@egogorushka88", TG_SESSION="1:hash:session",
                  NOTIFY_CHAT_ID="777", HOT_MODE="on", HOT_MIN_COMMENTS="4")

import core  # noqa: E402
import github_run  # noqa: E402
import web_source  # noqa: E402

NOW = time.time()


def ago(minutes):
    return datetime.fromtimestamp(NOW - minutes * 60, tz=timezone.utc).isoformat()


# ---------- 1. AccountReader.recent_posts: альбом = один пост, коментарі з поля replies ----------
class R:
    def __init__(self, n, comments=True):
        self.replies, self.comments = n, comments


class M:
    def __init__(self, id, text="", minutes=10, grouped_id=None, replies=None, photo=None):
        self.id, self.message, self.grouped_id, self.replies, self.photo = id, text, grouped_id, replies, photo
        self.action = None
        self.date = datetime.fromtimestamp(NOW - minutes * 60, tz=timezone.utc)


msgs = [  # Telethon віддає від нових до старих
    M(110, "Розетка Tapo P110 — 9.90€", 5, replies=R(7), photo="p"),
    M(109, "", 20, grouped_id=77, replies=R(0), photo="p1"),
    M(108, "Модем Huawei 4G — 32€", 20, grouped_id=77, replies=R(12), photo="p2"),
    M(107, "Канал без коментарів", 30, replies=None),
    M(90, "Старий пост", 60 * 30, replies=R(50)),           # старше за 24 год — не дивимось
]
reader = github_run.AccountReader((1, "h", ""), {})


async def fake_entity(ref):
    return "entity", "egogorushka88"


async def fake_iter(entity, limit=None):
    for m in msgs:
        yield m


reader._entity = fake_entity
reader.client.iter_messages = fake_iter
username, posts = asyncio.run(reader.recent_posts("@egogorushka88", NOW - 24 * 3600))
by_id = {p["id"]: p for p in posts}
assert set(by_id) == {107, 108, 110}, by_id.keys()
assert by_id[108]["comments"] == 12 and by_id[108]["text"].startswith("Модем") and by_id[108]["media"].id == 108
assert by_id[107]["comments"] is None and by_id[110]["comments"] == 7
print("reader OK: альбом з 2 фото = 1 пост, коментарі пораховано, старі пости не беремо")

# ---------- 2. Повний прогін бота ----------
feed = {}                       # канал -> пости з коментарями


class FakeReader:
    def __init__(self, creds, peers):
        self.client = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def download_media(self, msg, file=None):
        return b"\xff\xd8jpeg"

    async def recent_posts(self, ref, since, limit=300):
        ch = ref.lstrip("@")
        return ch, [p for p in feed.get(ch, []) if github_run.post_time(p["date"]) >= since]


sent = []


class FakeBot:
    def __init__(self, token):
        pass

    async def call(self, method, params=None, http_timeout=30):
        return {"ok": True, "result": {"username": "EstationC_bot"} if method == "getMe" else []}

    async def send(self, chat_id, text, button_url=None):
        sent.append((text, button_url))
        return True, {"ok": True}

    async def send_photo(self, chat_id, caption, photo_url=None, photo_bytes=None, button_url=None):
        sent.append((caption, button_url))
        return True, {"ok": True}


web_source.fetch_new = lambda channel, last_id, max_pages=5: (channel, [])
github_run.AccountReader = FakeReader
github_run.BotAPI = FakeBot
tmp = Path(tempfile.mkdtemp())
github_run.STATE_DIR = tmp
github_run.STATE_FILE = tmp / "state.json"


def run(force_scan=True):
    r = github_run.Run()
    r.state["announced"] = True
    if force_scan:
        r.state["hot_scan_at"] = 0
        github_run.save_state(r.state, "")
    asyncio.run(r.main())
    return r


def post(id, text, comments, minutes, media=None):
    return {"id": id, "text": text, "date": ago(minutes), "comments": comments, "media": media}


feed["ZBSkitai"] = [
    post(1, "Розетка TP-Link Tapo P110 — €9.90", 30, 60 * 5),       # жваве, але старе (5 год) — перший запуск
    post(2, "Зубна щітка Oclean X — 19€", 9, 60),                  # жваве і свіже → надіслати
]
feed["egogorushka88"] = [
    post(10, "Модем Huawei 4G — 32€", 4, 20),                       # рівно 4 — замало
    post(11, "OUKITEL P800E - €163\n800W / 512Wh", 40, 15),         # станція — не дублюємо
    post(12, "", 6, 10, media="photo"),                             # без тексту, з фото
]
r = run()
links = [l for _, l in sent]
assert links == ["https://t.me/ZBSkitai/2", "https://t.me/egogorushka88/12"], links
first, second, third = sent[0][0].splitlines()[:3]
assert first == "💬 <b>Зубна щітка Oclean X</b>" and second == "💶 <b>19€</b>", (first, second)
assert third.startswith("🗨 <b>9 коментарів</b>"), third
assert sent[1][0].splitlines()[0] == "💬 <b>Пост без тексту</b>", sent[1][0].splitlines()[0]
print("run OK: надіслано 2 (щітка, пост з фото); не надіслано: 4 коментарі, станцію, старе обговорення")
print("   ", " / ".join(sent[0][0].splitlines()[:3]))

# Повторний прогін: коментарів побільшало — те саме обговорення вдруге не шлемо; модем набрав 5 — шлемо
sent.clear()
feed["ZBSkitai"][1]["comments"] = 25
feed["egogorushka88"][0]["comments"] = 5
r = run()
assert [l for _, l in sent] == ["https://t.me/egogorushka88/10"], sent
assert sent[0][0].splitlines()[2].startswith("🗨 <b>5 коментарів</b>")
print("again OK: модем набрав 5 коментарів — надіслано; щітку вдруге не надіслано")

# Сканування не частіше ніж раз на 3 хв
sent.clear()
feed["ZBSkitai"].append(post(3, "Кавоварка — 120€", 8, 1))
r = run(force_scan=False)
assert sent == [], sent
print("pace OK: коментарі перевіряються не частіше ніж раз на 3 хв")

# Дублікати: той самий пост двічі поспіль (як «Знижка від larik2133» у Знижках Мао) — надсилаємо один раз
sent.clear()
feed["ZBSkitai"] += [post(4, "Знижка від larik2133", 13, 2), post(5, "Знижка від larik2133", 13, 1)]
r = run()
assert [l for _, l in sent] == ["https://t.me/ZBSkitai/3", "https://t.me/ZBSkitai/4"], [l for _, l in sent]
print("dedup OK: однакове обговорення двічі поспіль — надіслано один раз")
